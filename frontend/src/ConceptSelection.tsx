import { useState } from 'react'
import {
  ApiError,
  resolveProjectCreationAction,
  type ResolveProjectCreationActionRequest,
} from './api/client'

export interface ConceptSelectionProps {
  projectId: string
  workflowRunId: string
  actionId: string
  allowedDecisions: string[]
  options: string[]
  onResolved: () => void
}

export default function ConceptSelection({
  projectId,
  workflowRunId,
  actionId,
  allowedDecisions,
  options,
  onResolved,
}: ConceptSelectionProps) {
  const canSelect = allowedDecisions.includes('select')
  const canFuse = allowedDecisions.includes('fuse')

  const [mode, setMode] = useState<'select' | 'fuse'>(
    canSelect ? 'select' : 'fuse',
  )
  const [selectedOption, setSelectedOption] = useState<string | null>(null)
  const [fusedConcept, setFusedConcept] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSelect() {
    if (!selectedOption || pending) return
    setPending(true)
    setError(null)
    try {
      const body: ResolveProjectCreationActionRequest = {
        decision: 'select',
        option_id: selectedOption,
      }
      const result = await resolveProjectCreationAction(
        projectId,
        workflowRunId,
        actionId,
        body,
      )
      if (result.status === 'concept_selected') {
        onResolved()
      }
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 409) {
        setError('该操作已被处理，无法重复提交。')
      } else {
        setError('操作失败，请重试。')
      }
    } finally {
      setPending(false)
    }
  }

  async function handleFuse() {
    const trimmed = fusedConcept.trim()
    if (!trimmed || trimmed.length < 1 || trimmed.length > 4000 || pending) return
    setPending(true)
    setError(null)
    try {
      const body: ResolveProjectCreationActionRequest = {
        decision: 'fuse',
        fused_concept: trimmed,
      }
      const result = await resolveProjectCreationAction(
        projectId,
        workflowRunId,
        actionId,
        body,
      )
      if (result.status === 'concept_selected') {
        onResolved()
      }
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 409) {
        setError('该操作已被处理，无法重复提交。')
      } else {
        setError('操作失败，请重试。')
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="concept-selection" aria-labelledby="concept-selection-title">
      <h2 id="concept-selection-title">概念选择</h2>
      <p className="muted">
        请选择一个概念方案，或融合多个概念创作自定义方案。
      </p>

      {canSelect && canFuse && (
        <div className="mode-toggle" role="tablist" aria-label="选择模式">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'select'}
            className={mode === 'select' ? 'mode-tab active' : 'mode-tab'}
            onClick={() => setMode('select')}
          >
            选择概念
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'fuse'}
            className={mode === 'fuse' ? 'mode-tab active' : 'mode-tab'}
            onClick={() => setMode('fuse')}
          >
            自定义融合
          </button>
        </div>
      )}

      {mode === 'select' && canSelect && (
        <div className="select-mode" role="tabpanel" aria-label="选择概念方案">
          <ul className="concept-options" aria-label="可选概念方案">
            {options.map((option) => (
              <li key={option}>
                <button
                  type="button"
                  className={`concept-card${selectedOption === option ? ' selected' : ''}`}
                  onClick={() => setSelectedOption(option)}
                  aria-pressed={selectedOption === option}
                >
                  <span className="concept-card-label">{option}</span>
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={handleSelect}
            disabled={!selectedOption || pending}
          >
            {pending ? '提交中…' : '选择这个概念'}
          </button>
        </div>
      )}

      {mode === 'fuse' && canFuse && (
        <div className="fuse-mode" role="tabpanel" aria-label="自定义融合概念">
          <label className="fuse-label">
            融合概念
            <textarea
              className="fuse-textarea"
              value={fusedConcept}
              onChange={(e) => setFusedConcept(e.target.value)}
              maxLength={4000}
              placeholder="请输入您的融合概念方案（1–4000 字）"
              rows={8}
            />
            <span className="char-count">
              {fusedConcept.length}/4000
            </span>
          </label>
          <button
            type="button"
            onClick={handleFuse}
            disabled={fusedConcept.trim().length === 0 || pending}
          >
            {pending ? '提交中…' : '提交融合概念'}
          </button>
        </div>
      )}

      {error && (
        <p className="notice" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
