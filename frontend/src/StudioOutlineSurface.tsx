import { useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { TextMotionSurface, TextSweep } from './StudioMotion'

export default function StudioOutlineSurface({ choosing, children, footer, motionKey }: { choosing: boolean; children: ReactNode; footer?: ReactNode; motionKey?: string }) {
  const [hasShownChoices, setHasShownChoices] = useState(choosing)
  if (choosing && !hasShownChoices) setHasShownChoices(true)
  const returning = !choosing && hasShownChoices
  return <TextMotionSurface motionKey={motionKey} revealAfter={returning ? 1000 : 0} className={`studio-outline-generation original-outline-surface${choosing ? ' is-split' : returning ? ' is-returning' : ''}`}>
    <div className="studio-outline-prompt" data-text-inactive={choosing} aria-hidden={choosing}><h1><TextSweep text="想怎么写？" /></h1></div>
    <div className="studio-outline-frame">
      <div className="studio-split-panels" aria-hidden="true"><i /><i /><i /></div>
      <div className="studio-outline-generator-content">{children}</div>
    </div>
    <div className="studio-outline-refresh-slot">{footer}</div>
  </TextMotionSurface>
}

export function OutlineSendButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} className="outline-send" aria-label="生成大纲方案" title="生成大纲方案"><img src="/ui/studio/outline-send.svg" alt="" /></button>
}

export function OutlineRefreshButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} className="outline-refresh" aria-label="换一组" title="换一组"><img src="/ui/studio/outline-refresh.svg" alt="" /></button>
}

export function OutlineChoiceLabel() {
  return <span>采用此方案</span>
}
