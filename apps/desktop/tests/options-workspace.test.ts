import assert from "node:assert/strict"
import test from "node:test"
import { defaultOptionsDraft, parseOptionsDraft, pairOptionChain } from "../src/lib/optionsWorkspace.ts"
import type { OptionRow } from "../src/lib/options.ts"
import { parseAgentWorkspaceCommandBatch } from "../src/workspace/agentCommands.ts"

test("option drafts recover context and positions without trusting corrupt storage", () => {
  for(const raw of [null,"{broken","[]","null"]) assert.deepEqual(parseOptionsDraft(raw),defaultOptionsDraft)
  const d=parseOptionsDraft(JSON.stringify({tab:"portfolio",symbol:"159915.XSHE",selectedDay:"2026-10-23",field:"settlement",legs:[
    {underlying:"159915.XSHE",contract:"100001",quantity:-2,label:"old"},
    {underlying:"159915.XSHE",contract:"100001",quantity:10},
    {underlying:"../../file",contract:"100002",quantity:2},
  ],paths:999999,correlation:-1,sampling:"importance",surfaceModel:"svi"}))
  assert.equal(d.tab,"portfolio");assert.equal(d.field,"settlement");assert.equal(d.sampling,"importance")
  assert.equal(d.selectedDay,"2026-10-23");assert.equal(d.surfaceModel,"svi")
  assert.equal(d.paths,defaultOptionsDraft.paths);assert.equal(d.correlation,defaultOptionsDraft.correlation)
  assert.deepEqual(d.legs,[{underlying:"159915.XSHE",contract:"100001",quantity:-2,label:"old"}])
})

const row=(id:string,kind:"C"|"P",multiplier=10000,expiry="2026-11-25")=>({order_book_id:id,option_type:kind,multiplier,expiry,strike:3} as OptionRow)
test("option chain pairs dated terms and retains adjusted or ambiguous contracts",()=>{
  const input=[row("c","C"),row("p","P"),row("ac","C",10220),row("next","P",10000,"2026-12-23")]
  const pairs=pairOptionChain(input)
  assert.equal(pairs.length,3)
  assert.equal(pairs[0].call?.order_book_id,"c");assert.equal(pairs[0].put?.order_book_id,"p")
  assert.equal(pairs[1].call?.order_book_id,"ac");assert.equal(pairs[1].put,undefined)
  const ambiguous=pairOptionChain([...input,row("c2","C")])
  assert.equal(ambiguous.filter(p=>p.ambiguous).length,3)
  assert.ok(ambiguous.filter(p=>p.ambiguous).every(p=>!p.call||!p.put))
  assert.equal(ambiguous.flatMap(p=>[p.call,p.put]).filter(Boolean).length,5)
})

test("workspace commands can directly open independent option research",()=>{
  const batch=parseAgentWorkspaceCommandBatch({version:1,requestId:"options",commands:[
    {type:"switch_mode",mode:"options"},{type:"open_widget",widgetId:"options.workbench",mode:"options"},
  ]})
  assert.equal(batch?.commands.length,2)
})
