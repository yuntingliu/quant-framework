/** Bind a hosted tool snapshot to one deployment, independently of host-wide env. */
export function bindToolToInstance(tool, apiOrigin, instanceId) {
  const url = new URL(apiOrigin)
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("The private tool API must be an HTTP loopback origin without credentials or a path")
  }
  if (!/^[a-z0-9][a-z0-9_-]{1,63}$/.test(instanceId)) {
    throw new Error("A deployment instance ID is required for a bound publication")
  }
  const prefix = /^function origin\(\)\{[^\n]*\}\n/
  const run = "try{return await execute(input)}"
  if (!prefix.test(tool.code) || !tool.code.includes(run)) {
    throw new Error("Unsupported AlphaLab tool wrapper; refusing an unbound publication")
  }
  const code = tool.code.replace(prefix, `function origin(){return ${JSON.stringify(url.origin)}}\n`)
    .replace(run, "try{await verifyInstance();return await execute(input)}")
  const guard = `\nasync function verifyInstance(){
    const response=await fetch(origin()+'/api/agent/identity',{redirect:'error',signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw new Error('AlphaLab instance verification failed; no operation was executed');
    const value=await response.json();
    if(value.instance_id!==${JSON.stringify(instanceId)})throw new Error('AlphaLab instance mismatch; no operation was executed');
  }\n`
  return { ...tool, code: code + guard }
}
