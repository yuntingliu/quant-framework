import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Code2, Database, FlaskConical, GitBranch, Layers } from 'lucide-react'
import { SourceHistoryPanel } from '/src/components/shared/SourceHistoryDialog'
import { LanguageProvider } from '/src/contexts/LanguageContext'
import '/src/index.css'

const css = `
body { margin: 0; background: #f5f6f8; color: #202631; -webkit-font-smoothing: antialiased; }
.preview-nav { height: 62px; border-bottom: 1px solid #e6e9ee; background: #fff; display: flex; align-items: center; justify-content: space-between; padding: 0 38px; }
.preview-brand { display: flex; align-items: center; gap: 11px; font-size: 17px; font-weight: 650; letter-spacing: -.4px; }
.preview-logo { background: #2454bd; color: white; width: 29px; height: 29px; display: grid; place-items: center; border-radius: 8px; }
.preview-divider { width: 1px; height: 17px; background: #e2e5eb; margin: 0 10px; }
.preview-nav-label { color: #7d8592; font-size: 12px; font-weight: 400; letter-spacing: 0; }
.preview-status { display: flex; align-items: center; gap: 7px; font-size: 11px; color: #757e8a; }
.preview-status i { width: 6px; height: 6px; background: #559375; border-radius: 50%; }
.preview-main { max-width: 1220px; margin: 0 auto; padding: 30px 36px 32px; }
.preview-intro { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.preview-intro h1 { font-size: 26px; font-weight: 650; letter-spacing: -.7px; margin: 0 0 6px; }
.preview-intro p { color: #7b8390; font-size: 12px; margin: 0; }
.preview-project { display: flex; align-items: center; gap: 7px; color: #67717f; border: 1px solid #e1e5eb; border-radius: 7px; padding: 8px 11px; background: #fff; font-size: 11px; }
.preview-tabs { display: flex; gap: 26px; border-bottom: 1px solid #dfe4eb; margin-bottom: 22px; }
.preview-tabs button { position: relative; display: flex; align-items: center; gap: 7px; cursor: pointer; padding: 0 1px 13px; color: #7b8390; border: 0; background: none; font-size: 12px; font-weight: 500; }
.preview-tabs button[aria-pressed="true"] { color: #2454bd; }
.preview-tabs button[aria-pressed="true"]::after { content: ''; position: absolute; bottom: -1px; height: 2px; background: #2454bd; left: 0; right: 0; }
.preview-tabs button:focus-visible { outline: 2px solid #2454bd; outline-offset: 4px; }
.preview-card { border: 1px solid #dfe4eb; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 18px #162f5105; }
.preview-footnote { font-size: 10px; color: #8d95a0; margin: 15px 0 0; text-align: right; }
@media(max-width:760px) { .preview-nav { padding: 0 18px; } .preview-nav-label, .preview-divider, .preview-project { display: none; } .preview-main { padding: 24px 14px; } .preview-intro h1 { font-size: 23px; } .preview-intro p { line-height: 1.8; } .preview-tabs { gap: 22px; } .preview-footnote { text-align: left; } }
`
export function Demo() {
  const [kind, setKind] = useState<'strategy' | 'recipe' | 'validation'>('strategy')
  const tabs = [{id:'strategy', label:'策略与因子', icon:Code2}, {id:'recipe',label:'数据采集',icon:Database}, {id:'validation',label:'回测验证',icon:FlaskConical}] as const
  return <LanguageProvider>
    <style>{css}</style>
    <header className="preview-nav"><div className="preview-brand"><span className="preview-logo"><Layers size={18} /></span>AlphaLab<span className="preview-divider" /><span className="preview-nav-label">研究工作台</span></div><span className="preview-status"><i />本地功能预览</span></header>
    <main className="preview-main">
      <div className="preview-intro"><div><h1>研究源码</h1><p>从一个版本到下一个版本，清楚了解每一处变化。</p></div><span className="preview-project"><GitBranch size={14} />动量策略研究 · 示例项目</span></div>
      <nav className="preview-tabs" aria-label="源码类型">{tabs.map(({id,label,icon:Icon})=><button type="button" key={id} aria-pressed={kind === id} onClick={()=>setKind(id)}><Icon size={15}/>{label}</button>)}</nav>
      <div className="preview-card"><SourceHistoryPanel projectId="history-demo" projectName="动量策略研究" strategyId="main" kind={kind}/></div>
      <p className="preview-footnote">示例数据 · 使用与工作台相同的版本历史组件</p>
    </main>
  </LanguageProvider>
}
createRoot(document.getElementById('root')!).render(<Demo />)
