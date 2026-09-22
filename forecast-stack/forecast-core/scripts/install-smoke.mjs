import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
const source=fileURLToPath(new URL('..',import.meta.url));
const root=fs.mkdtempSync(path.join(os.tmpdir(),'vati-installed-'));
const env={...process.env,npm_config_cache:path.join(root,'npm-cache')};
try {
  execFileSync('npm',['pack','--pack-destination',root],{cwd:source,stdio:'pipe',env});
  const artifact=fs.readdirSync(root).find(f=>f.endsWith('.tgz'));assert.ok(artifact);
  fs.writeFileSync(path.join(root,'package.json'),'{"private":true,"type":"module"}');
  execFileSync('npm',['install','--offline','--ignore-scripts','--no-audit','--no-fund',path.join(root,artifact)],{cwd:root,stdio:'pipe',env});
  const pkg=JSON.parse(fs.readFileSync(path.join(source,'package.json'),'utf8'));
  const code=Object.keys(pkg.exports).map(s=>`await import(${JSON.stringify(pkg.name+s.slice(1))});`).join('\n');
  execFileSync(process.execPath,['--input-type=module','-e',code],{cwd:root,stdio:'pipe'});
  const executable=path.join(root,'node_modules/.bin/vati');
  const result=JSON.parse(execFileSync(executable,['demo','--state',path.join(root,'demo')],{cwd:root,encoding:'utf8'}));
  assert.equal(result.synthetic,true);assert.equal(result.score.rows[0].outcome,1);assert.equal(result.score.rows[0].brier,.5625);
  console.log(JSON.stringify({artifact,version:pkg.version,exports:Object.keys(pkg.exports).length,installed_cli:'passed',offline_lifecycle:'passed'}));
}finally{fs.rmSync(root,{recursive:true,force:true});}
