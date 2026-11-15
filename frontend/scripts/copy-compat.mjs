/* 保留 /app.js 与 /style.css 两个稳定入口，兼容旧书签与既有静态资源探针。 */
import { copyFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const dist = fileURLToPath(new URL('../../wfp/web/dist/', import.meta.url));
const assets = `${dist}assets`;
const js = readdirSync(assets).filter((f) => /^index-.*\.js$/.test(f)).sort().at(-1);
const css = readdirSync(assets).filter((f) => /^index-.*\.css$/.test(f)).sort().at(-1);
if (!js || !css) throw new Error('Vite 产物缺少入口 JS/CSS');
copyFileSync(`${assets}/${js}`, `${dist}app.js`);
copyFileSync(`${assets}/${css}`, `${dist}style.css`);
console.log(`compat entries: app.js <- ${js}, style.css <- ${css}`);
