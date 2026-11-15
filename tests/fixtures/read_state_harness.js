// Modello DOM locale: esegue lo stesso JavaScript consegnato allo scraper.
const fs = require('node:fs');
const read = eval('(' + fs.readFileSync(0, 'utf8') + ')');
const source = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let variantReads = 0;
const scope = {PageNumber:1,PageCount:2,OnlyBuyList:false,SelectedFilters:{},Filters:{Expansions:[{Id:2364,Espansione:'Base'}]}};
const input={}; const root={};
const nodes=source.map(row=>{
 const product={...row.product}; const raw=product.Variants;
 Object.defineProperty(product,'Variants',{enumerable:true,get(){variantReads++;return raw;}});
 return {child:{product,expansion:{Expansion:row.expansion}},innerText:row.evaluation?'Riceverai una valutazione':'',
 querySelector(selector){
  if(selector==='h4.name')return {textContent:row.name};
  if(selector==='.alt-name')return {textContent:row.name_en};
  if(selector==='.buy-price')return {textContent:row.price_text};
  if(selector==='img')return {src:row.image,getAttribute(){return row.image;}};
  if(selector.startsWith('select'))return {selectedOptions:[{textContent:'Italiano'}],options:[{textContent:'Italiano'}]};
  return {checked:false};
 }};
});
globalThis.window={angular:true};
globalThis.angular={element(el){return {scope(){return el===input?scope:el.child;},injector(){return {get(){return {pendingRequests:[]};}};}};}};
globalThis.document={body:{innerText:''},querySelector(selector){return selector==='#simplesearch'?input:root;},getElementById(){return root;},
 querySelectorAll(selector){return selector.includes('ng-repeat')?nodes:[];}};
const probe=read({probe:true});
if(variantReads!==0)throw Error('Probe reads variants');
const full=read({seen:[probe.all_keys[0]]});
if(variantReads!==1 || full.rows.length!==source.length-1 || full.all_keys.length!==source.length)throw Error('Projection wrong');
console.log('JS reale READ_STATE: probe senza Variants, snapshot completo solo per carte nuove.');

const diagnostic=read(null);
if(diagnostic.rows.length!==source.length)throw Error("Null argument broke diagnostics");
