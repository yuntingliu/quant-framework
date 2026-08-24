import { Check, FlaskConical, FolderKanban, Play, Save } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFactorLab } from "@/contexts/FactorLabContext"
import { useWorkspace } from "@/contexts/WorkspaceContext"
import { useDataProfile } from "@/lib/data-profile"
import { Widget } from "@/widgets/Widget"

import {
  FactorEditorWidget,
  FactorEvidenceWidget,
  FactorLibraryWidget,
  FactorSnapshotWidget,
} from "./FactorLabPanels"

type StepState = "done" | "active" | "pending" | "warning"

function WorkflowStep({ number, title, detail, state }: { number: number; title: string; detail: string; state: StepState }) {
  return (
    <Card className={`factor-workflow-step ${state}`}>
      <CardContent className="factor-workflow-step-content">
        <span className="factor-workflow-step-number">{state === "done" ? <Check size={13} /> : number}</span>
        <span><strong>{title}</strong><small>{detail}</small></span>
      </CardContent>
    </Card>
  )
}

export function FactorWorkbenchWidget() {
  const lab = useFactorLab()
  const { setActiveMode } = useWorkspace()
  const [profile] = useDataProfile()
  const validated = Boolean(lab.result && !lab.resultStale)
  const savedInProject = lab.projectFactors.some((factor) => factor.name === lab.draft.name)
  const canSave = Boolean(lab.project?.editable && validated)
  const runDisabled = !lab.draft.name.trim() || (lab.draft.source === "expression" && !lab.draft.expression.trim())

  return (
    <Widget headerless>
      <div className="factor-workbench-shell">
        <header className="factor-workbench-header">
          <div className="factor-workbench-title">
            <span className="factor-workbench-mark"><FlaskConical size={18} /></span>
            <div>
              <div><h1>因子研究</h1><Badge variant="outline">{profile === "demo" ? "示例数据" : "本地数据"}</Badge></div>
              <p>选择来源 → 配置并运行单因子评估 → 阅读证据 → 决定是否加入项目</p>
            </div>
          </div>
          <div className="factor-workbench-context">
            <div><span>当前研究项目</span><strong>{lab.project?.name ?? "尚未选择"}</strong></div>
            <Button variant="outline" size="sm" onClick={() => setActiveMode("project")}><FolderKanban />{lab.project ? "项目设置" : "选择项目"}</Button>
          </div>
        </header>

        <div className="factor-workflow-strip" aria-label="因子研究流程">
          <WorkflowStep number={1} title="选择来源" detail={`${lab.draft.name} · ${lab.draft.source === "technical" ? "技术" : lab.draft.source === "fundamental" ? "基本面" : "表达式"}`} state="done" />
          <WorkflowStep number={2} title="配置与评估" detail={`${lab.draft.startDate} 至 ${lab.draft.endDate}`} state={validated ? "done" : lab.resultStale ? "warning" : "active"} />
          <WorkflowStep number={3} title="阅读证据" detail={lab.result ? `${lab.result.periods} 个有效截面` : "等待运行评估"} state={validated ? "done" : lab.result ? "warning" : "pending"} />
          <WorkflowStep number={4} title="加入项目" detail={!lab.project ? "先选择研究项目" : savedInProject ? "当前项目已包含" : lab.project.editable ? "验证后可加入" : "项目只读"} state={savedInProject ? "done" : canSave ? "active" : "pending"} />
        </div>

        <Tabs className="factor-workbench-tabs" value={lab.workspaceView} onValueChange={(value) => lab.setWorkspaceView(value as "build" | "results")}>
          <div className="factor-workbench-toolbar">
            <TabsList>
              <TabsTrigger value="build">构建与验证</TabsTrigger>
              <TabsTrigger value="results" disabled={!lab.result}>研究结果{lab.resultStale ? <span className="factor-tab-warning">已过期</span> : null}</TabsTrigger>
            </TabsList>
            <div className="factor-workbench-actions">
              <Button variant="outline" size="sm" onClick={() => void lab.evaluate()} disabled={runDisabled} isLoading={lab.running}><Play />{lab.result ? "重新评估" : "运行评估"}</Button>
              <Button size="sm" onClick={() => void lab.saveToProject()} disabled={!canSave} isLoading={lab.saving} title={canSave ? "把当前已验证定义保存到项目" : "需要当前定义评估通过且项目可编辑"}><Save />{lab.editingOriginalName ? "更新项目因子" : "加入项目"}</Button>
            </div>
          </div>

          {lab.error ? <div className="workbench-message error factor-workbench-error">{lab.error}</div> : null}

          <TabsContent className="factor-workbench-content" value="build">
            <div className="factor-build-grid">
              <FactorLibraryWidget embedded />
              <FactorEditorWidget embedded />
            </div>
          </TabsContent>
          <TabsContent className="factor-workbench-content" value="results">
            <div className="factor-results-grid">
              <FactorEvidenceWidget embedded />
              <FactorSnapshotWidget embedded />
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </Widget>
  )
}
