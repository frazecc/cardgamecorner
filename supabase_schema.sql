-- CardGameCorner: snapshot completo pubblicato solo dopo i controlli finali.
create extension if not exists pg_trgm with schema extensions;

create table public.cgc_imports (
  id text primary key,
  source_run_id text not null,
  expected_cards integer not null check(expected_cards > 0),
  expected_prices integer not null check(expected_prices > 0),
  expected_queries integer not null check(expected_queries > 0),
  state text not null default 'LOADING' check(state in ('LOADING','COMPLETE')),
  started_at timestamptz not null default now(),
  completed_at timestamptz
);
create table public.cgc_cards (
  import_id text not null references public.cgc_imports(id),
  card_key text not null,
  name text not null,
  name_en text,
  expansion text not null,
  serial_number text,
  image text,
  source jsonb not null,
  primary key(import_id,card_key)
);
create table public.cgc_prices (
  import_id text not null,
  entry_key text not null,
  card_key text not null,
  variant_id text not null,
  language text not null,
  condition text not null,
  foil text not null,
  first_edition text not null,
  buylist numeric not null check(buylist >= 0),
  credit numeric not null check(credit >= 0),
  evaluation boolean not null,
  excluded boolean not null,
  id_occurrences integer not null,
  source jsonb not null,
  primary key(import_id,entry_key),
  foreign key(import_id,card_key) references public.cgc_cards(import_id,card_key)
);
create table public.cgc_active (
  singleton boolean primary key default true check(singleton),
  import_id text not null references public.cgc_imports(id),
  published_at timestamptz not null default now()
);
create index cgc_cards_name_idx on public.cgc_cards using gin(lower(name) extensions.gin_trgm_ops);
create index cgc_cards_name_raw_idx on public.cgc_cards using gin(name extensions.gin_trgm_ops);
create index cgc_cards_expansion_idx on public.cgc_cards(import_id,expansion,card_key);
create index cgc_prices_card_idx on public.cgc_prices(import_id,card_key,entry_key);
create index cgc_prices_filters_idx on public.cgc_prices(import_id,language,condition,entry_key);
create index cgc_active_import_idx on public.cgc_active(import_id);

alter table public.cgc_imports enable row level security;
alter table public.cgc_cards enable row level security;
alter table public.cgc_prices enable row level security;
alter table public.cgc_active enable row level security;
revoke all on public.cgc_imports,public.cgc_cards,public.cgc_prices,public.cgc_active from anon,authenticated;
grant select on public.cgc_imports,public.cgc_cards,public.cgc_prices,public.cgc_active to anon,authenticated;
grant all on public.cgc_imports,public.cgc_cards,public.cgc_prices,public.cgc_active to service_role;
create policy cgc_active_read on public.cgc_active for select to anon,authenticated using(true);
create policy cgc_import_read on public.cgc_imports for select to anon,authenticated
  using(id=(select import_id from public.cgc_active where singleton));
create policy cgc_cards_read on public.cgc_cards for select to anon,authenticated
  using(import_id=(select import_id from public.cgc_active where singleton));
create policy cgc_prices_read on public.cgc_prices for select to anon,authenticated
  using(import_id=(select import_id from public.cgc_active where singleton));

create function public.cgc_open(p_id text,p_source_run_id text,p_cards integer,p_prices integer,p_queries integer)
returns text language plpgsql security invoker set search_path='' as $$
declare old public.cgc_imports;
begin
  insert into public.cgc_imports(id,source_run_id,expected_cards,expected_prices,expected_queries)
  values(p_id,p_source_run_id,p_cards,p_prices,p_queries) on conflict(id) do nothing;
  select * into old from public.cgc_imports where id=p_id for update;
  if (old.expected_cards,old.expected_prices,old.expected_queries)
     is distinct from (p_cards,p_prices,p_queries) then
    raise exception 'Import ID gia usato per dati diversi';
  end if;
  return old.state;
end $$;

create function public.cgc_ingest(p_id text,p_batch jsonb)
returns integer language plpgsql security invoker set search_path='' as $$
declare n integer;
begin
  perform 1 from public.cgc_imports where id=p_id and state='LOADING' for update;
  if not found then raise exception 'Import assente o gia pubblicato'; end if;
  if jsonb_typeof(p_batch)<>'array' or jsonb_array_length(p_batch)>1000 then
    raise exception 'Blocco non valido';
  end if;
  if (select count(*)<>count(distinct x->>'key') from jsonb_array_elements(p_batch) x) then
    raise exception 'Carte duplicate nel blocco';
  end if;
  insert into public.cgc_cards(import_id,card_key,name,name_en,expansion,serial_number,image,source)
  select p_id,x->>'key',x->'raw'->>'name',x->'raw'->>'name_en',x->'raw'->>'expansion',
         x->'raw'->'product'->>'SerialNumber',x->'raw'->>'image',x->'raw'
  from jsonb_array_elements(p_batch) x
  on conflict(import_id,card_key) do update set name=excluded.name,name_en=excluded.name_en,
    expansion=excluded.expansion,serial_number=excluded.serial_number,image=excluded.image,source=excluded.source;
  delete from public.cgc_prices where import_id=p_id and card_key in
    (select x->>'key' from jsonb_array_elements(p_batch) x);
  insert into public.cgc_prices(import_id,entry_key,card_key,variant_id,language,condition,foil,
    first_edition,buylist,credit,evaluation,excluded,id_occurrences,source)
  select p_id,v->>'key',x->>'key',v->'raw'->>'IdProduct',coalesce(v->'raw'->>'Language',''),
    coalesce(v->'raw'->>'Condition',''),coalesce(v->'raw'->>'Foil',''),coalesce(v->'raw'->>'FirstEdition',''),
    (v->'raw'->>'BuyList')::numeric,(v->'raw'->>'Credit')::numeric,
    (v->'raw'->>'Evaluation')::boolean,(v->'raw'->>'NoBuyList')::boolean,
    count(*) over(partition by x->>'key',v->'raw'->>'IdProduct'),v->'raw'
  from jsonb_array_elements(p_batch) x cross join lateral jsonb_array_elements(x->'variants') v;
  n:=jsonb_array_length(p_batch);
  return n;
end $$;

create function public.cgc_complete(p_id text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.cgc_imports; nc bigint; np bigint;
begin
  select * into strict r from public.cgc_imports where id=p_id for update;
  select count(*) into nc from public.cgc_cards where import_id=p_id;
  select count(*) into np from public.cgc_prices where import_id=p_id;
  if nc<>r.expected_cards or np<>r.expected_prices then
    raise exception 'Conteggi diversi: carte %, prezzi %',nc,np;
  end if;
  -- Un import gia completo e ritentabile, ma non ripubblica snapshot superati.
  if r.state='LOADING' then
    update public.cgc_imports set state='COMPLETE',completed_at=now() where id=p_id;
    insert into public.cgc_active(singleton,import_id) values(true,p_id)
    on conflict(singleton) do update set import_id=excluded.import_id,published_at=now();
  end if;
  return jsonb_build_object('cards',nc,'prices',np,'state','COMPLETE');
end $$;
revoke all on function public.cgc_open(text,text,integer,integer,integer) from public,anon,authenticated;
revoke all on function public.cgc_ingest(text,jsonb) from public,anon,authenticated;
revoke all on function public.cgc_complete(text) from public,anon,authenticated;
grant execute on function public.cgc_open(text,text,integer,integer,integer) to service_role;
grant execute on function public.cgc_ingest(text,jsonb) to service_role;
grant execute on function public.cgc_complete(text) to service_role;

create view public.cgc_buylist with(security_invoker=true) as
select p.import_id,p.entry_key,p.card_key,p.variant_id,c.name,c.name_en,c.expansion,c.serial_number,c.image,
       p.language,p.condition,p.foil,p.first_edition,p.buylist,p.credit,p.evaluation,p.excluded,
       p.id_occurrences,p.source->'BuyListLock' as buylist_lock,p.source->'Quantity' as quantity,
       btrim(p.language)<>'' as language_specified,btrim(p.condition)<>'' as condition_specified,
       case when p.excluded then 'ESCLUSA DALLA BUYLIST' when p.evaluation then 'VALUTAZIONE SU RICHIESTA'
         when p.buylist>0 or p.credit>0 then 'PREZZO DISPONIBILE' else 'SENZA PREZZO BUYLIST' end as status
from public.cgc_prices p join public.cgc_cards c using(import_id,card_key)
where p.import_id=(select import_id from public.cgc_active where singleton);
grant select on public.cgc_buylist to anon,authenticated,service_role;

comment on table public.cgc_cards is 'Anagrafica e dati originali, Variants separato in cgc_prices. Visibile solo lo snapshot pubblicato.';
comment on table public.cgc_prices is 'Tutte le varianti pubblicate dal sito, anche prezzi zero, valutazioni e dimensioni mancanti.';
