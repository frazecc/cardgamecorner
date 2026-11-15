-- Aggiornamenti incrementali: un archivio corrente e staging delle sole differenze.
-- Il precedente risultato rimane visibile fino al commit finale.
create table public.cgc_sync_jobs (
 id text primary key, source_run_id text not null, base_id text not null,
 base_published_at timestamptz not null, expected_cards integer not null check(expected_cards>0),
 expected_prices integer not null check(expected_prices>0),expected_queries integer not null check(expected_queries>0),
 state text not null default 'LOADING' check(state in('LOADING','COMPLETE')),
 started_at timestamptz not null default now(),completed_at timestamptz,
 changed_cards integer,changed_prices integer
);
create table public.cgc_sync_seen (
 job_id text not null references public.cgc_sync_jobs(id),card_key text not null,
 primary key(job_id,card_key)
);
create table public.cgc_sync_changes (
 job_id text not null references public.cgc_sync_jobs(id),card_key text not null,
 payload jsonb not null,prices_changed boolean not null,
 primary key(job_id,card_key)
);
alter table public.cgc_sync_jobs enable row level security;
alter table public.cgc_sync_seen enable row level security;
alter table public.cgc_sync_changes enable row level security;
revoke all on public.cgc_sync_jobs,public.cgc_sync_seen,public.cgc_sync_changes from anon,authenticated;
grant all on public.cgc_sync_jobs,public.cgc_sync_seen,public.cgc_sync_changes to service_role;

create function public.cgc_delta_open(p_id text,p_source_run_id text,p_cards integer,p_prices integer,p_queries integer)
returns text language plpgsql security invoker set search_path='' as $$
declare a public.cgc_active; j public.cgc_sync_jobs;
begin
 select * into strict a from public.cgc_active where singleton for update;
 insert into public.cgc_sync_jobs(id,source_run_id,base_id,base_published_at,expected_cards,expected_prices,expected_queries)
 values(p_id,p_source_run_id,a.import_id,a.published_at,p_cards,p_prices,p_queries) on conflict(id) do nothing;
 select * into strict j from public.cgc_sync_jobs where id=p_id for update;
 if (j.expected_cards,j.expected_prices,j.expected_queries) is distinct from (p_cards,p_prices,p_queries) then
  raise exception 'Checkpoint diverso per lo stesso job';
 end if;
 if j.state='LOADING' and (j.base_id,j.base_published_at) is distinct from (a.import_id,a.published_at) then
  delete from public.cgc_sync_changes where job_id=p_id;
  delete from public.cgc_sync_seen where job_id=p_id;
  update public.cgc_sync_jobs set base_id=a.import_id,base_published_at=a.published_at where id=p_id;
 end if;
 return j.state;
end $$;

create function public.cgc_delta_ingest(p_id text,p_batch jsonb)
returns integer language plpgsql security invoker set search_path='' as $$
declare j public.cgc_sync_jobs; a public.cgc_active; x jsonb; c public.cgc_cards;
 pc boolean; n integer; nk integer; distinct_keys integer;
begin
 select * into strict j from public.cgc_sync_jobs where id=p_id for update;
 select * into strict a from public.cgc_active where singleton;
 if j.state<>'LOADING' or (j.base_id,j.base_published_at) is distinct from(a.import_id,a.published_at) then
  raise exception 'Job completato o archivio di base cambiato';
 end if;
 if jsonb_typeof(p_batch)<>'array' or jsonb_array_length(p_batch)>1000 then raise exception 'Blocco non valido';end if;
 if (select count(*)<>count(distinct v->>'key') from jsonb_array_elements(p_batch)v) then
  raise exception 'Carte ripetute nel blocco';
 end if;
 for x in select value from jsonb_array_elements(p_batch) loop
  if not(x ? 'key' and x ? 'raw' and x ? 'variants') or jsonb_typeof(x->'variants')<>'array' then
   raise exception 'Carta incompleta';
  end if;
  select count(*),count(distinct v->>'key') into nk,distinct_keys from jsonb_array_elements(x->'variants') v;
  if nk<>distinct_keys then raise exception 'Chiavi variante duplicate';end if;
  insert into public.cgc_sync_seen(job_id,card_key)values(p_id,x->>'key')on conflict do nothing;
  select * into c from public.cgc_cards where import_id=j.base_id and card_key=x->>'key';
  if not found then pc:=true;
  else
   select count(*) into n from public.cgc_prices where import_id=j.base_id and card_key=x->>'key';
   pc:=n<>nk or exists(select 1 from jsonb_array_elements(x->'variants')v where not exists(
    select 1 from public.cgc_prices p where p.import_id=j.base_id and p.entry_key=v->>'key' and p.card_key=x->>'key'));
  end if;
  if c.card_key is null or c.source is distinct from x->'raw' or pc then
   insert into public.cgc_sync_changes(job_id,card_key,payload,prices_changed) values(p_id,x->>'key',x,pc)
   on conflict(job_id,card_key)do update set payload=excluded.payload,prices_changed=excluded.prices_changed;
  else
   -- Un blocco ritentato puo tornare uguale al dato corrente.
   delete from public.cgc_sync_changes where job_id=p_id and card_key=x->>'key';
  end if;
 end loop;
 return jsonb_array_length(p_batch);
end $$;

create function public.cgc_delta_complete(p_id text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare j public.cgc_sync_jobs; a public.cgc_active; nc bigint;np bigint; nd integer; nprice integer;
begin
 select * into strict j from public.cgc_sync_jobs where id=p_id for update;
 if j.state='COMPLETE' then
  return jsonb_build_object('state','COMPLETE','cards',j.expected_cards,'prices',j.expected_prices);
 end if;
 select * into strict a from public.cgc_active where singleton for update;
 if (j.base_id,j.base_published_at) is distinct from(a.import_id,a.published_at) then
  raise exception 'Archivio cambiato: riavviare l''importazione';
 end if;
 select count(*) into nc from public.cgc_sync_seen where job_id=p_id;
 if nc<>j.expected_cards then raise exception 'Carte confermate %, attese %',nc,j.expected_cards;end if;
 -- Verifica il numero previsto prima di toccare l'archivio corrente.
 select coalesce(sum(n),0) into np from (
  select jsonb_array_length(d.payload->'variants')::bigint as n from public.cgc_sync_changes d
    where d.job_id=p_id and d.prices_changed
  union all
  select count(*) from public.cgc_prices p join public.cgc_sync_seen s on s.job_id=p_id and s.card_key=p.card_key
    where p.import_id=j.base_id and not exists(select 1 from public.cgc_sync_changes d
      where d.job_id=p_id and d.card_key=p.card_key and d.prices_changed)
 )v;
 if np<>j.expected_prices then raise exception 'Voci prezzo %, attese %',np,j.expected_prices;end if;
 select count(*),count(*)filter(where prices_changed) into nd,nprice from public.cgc_sync_changes where job_id=p_id;
 insert into public.cgc_cards(import_id,card_key,name,name_en,expansion,serial_number,image,source)
 select j.base_id,card_key,payload->'raw'->>'name',payload->'raw'->>'name_en',payload->'raw'->>'expansion',
  payload->'raw'->'product'->>'SerialNumber',payload->'raw'->>'image',payload->'raw'
 from public.cgc_sync_changes where job_id=p_id
 on conflict(import_id,card_key)do update set name=excluded.name,name_en=excluded.name_en,expansion=excluded.expansion,
  serial_number=excluded.serial_number,image=excluded.image,source=excluded.source;
 delete from public.cgc_prices p where p.import_id=j.base_id and (
  exists(select 1 from public.cgc_sync_changes d where d.job_id=p_id and d.card_key=p.card_key and d.prices_changed)
  or not exists(select 1 from public.cgc_sync_seen s where s.job_id=p_id and s.card_key=p.card_key));
 insert into public.cgc_prices(import_id,entry_key,card_key,variant_id,language,condition,foil,first_edition,
  buylist,credit,evaluation,excluded,id_occurrences,source)
 select j.base_id,v->>'key',d.card_key,v->'raw'->>'IdProduct',coalesce(v->'raw'->>'Language',''),
  coalesce(v->'raw'->>'Condition',''),coalesce(v->'raw'->>'Foil',''),coalesce(v->'raw'->>'FirstEdition',''),
  (v->'raw'->>'BuyList')::numeric,(v->'raw'->>'Credit')::numeric,(v->'raw'->>'Evaluation')::boolean,
  (v->'raw'->>'NoBuyList')::boolean,count(*)over(partition by d.card_key,v->'raw'->>'IdProduct'),v->'raw'
 from public.cgc_sync_changes d cross join lateral jsonb_array_elements(d.payload->'variants')v
 where d.job_id=p_id and d.prices_changed;
 delete from public.cgc_cards c where c.import_id=j.base_id and not exists(
  select 1 from public.cgc_sync_seen s where s.job_id=p_id and s.card_key=c.card_key);
 select count(*) into nc from public.cgc_cards where import_id=j.base_id;
 select count(*) into np from public.cgc_prices where import_id=j.base_id;
 if nc<>j.expected_cards or np<>j.expected_prices then raise exception 'Verifica finale fallita';end if;
 update public.cgc_imports set source_run_id=j.source_run_id,expected_cards=nc,expected_prices=np,
  expected_queries=j.expected_queries,completed_at=now() where id=j.base_id;
 update public.cgc_active set published_at=now() where singleton;
 update public.cgc_sync_jobs set state='COMPLETE',completed_at=now(),changed_cards=nd,changed_prices=nprice where id=p_id;
 -- Elimina solo lo staging verificato; i backup completi restano negli artefatti GitHub.
 delete from public.cgc_sync_changes where job_id=p_id;
 delete from public.cgc_sync_seen where job_id=p_id;
 return jsonb_build_object('state','COMPLETE','cards',nc,'prices',np,'changed_cards',nd,'cards_with_changed_prices',nprice);
end $$;
revoke all on function public.cgc_delta_open(text,text,integer,integer,integer) from public,anon,authenticated;
revoke all on function public.cgc_delta_ingest(text,jsonb) from public,anon,authenticated;
revoke all on function public.cgc_delta_complete(text) from public,anon,authenticated;
grant execute on function public.cgc_delta_open(text,text,integer,integer,integer) to service_role;
grant execute on function public.cgc_delta_ingest(text,jsonb) to service_role;
grant execute on function public.cgc_delta_complete(text) to service_role;
