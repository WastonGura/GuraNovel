import type { ReactNode } from 'react'
import './SettingControls.css'
import './SettingActionIcon.css'

export type SettingActionIconName =
  | 'save' | 'history' | 'trash' | 'search' | 'more' | 'create' | 'note' | 'graph'
  | 'agent' | 'archive' | 'pin' | 'close' | 'copy'
  | 'plus' | 'minus' | 'fit'

export interface SettingActionIconProps {
  name: SettingActionIconName
  size?: 18 | 20
  className?: string
}

const paths: Record<SettingActionIconName, ReactNode> = {
  save: <>
    <path d="M5 3h12l4 4v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" />
    <path className="setting-action-icon__part setting-action-icon__save-tab" d="M7 3v6h10V3M14 3v3" />
    <path className="setting-action-icon__part setting-action-icon__save-panel" d="M7 21v-8h10v8" />
    <path className="setting-action-icon__part setting-action-icon__save-check" d="m8 16 2.5 2.5 5.5-6" />
  </>,
  history: <>
    <circle cx="12" cy="12" r="8.5" />
    <path className="setting-action-icon__part setting-action-icon__history-hand" d="M12 7v5l3 2" />
  </>,
  trash: <>
    <path d="m5 6 1 13a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-13M10 10v7M14 10v7" />
    <path className="setting-action-icon__part setting-action-icon__trash-lid" d="M3 6h18M9 6V3h6v3" />
  </>,
  search: <>
    <circle className="setting-action-icon__part setting-action-icon__search-lens" cx="10.5" cy="10.5" r="6.5" />
    <path className="setting-action-icon__part setting-action-icon__search-handle" d="m15.5 15.5 5 5" />
  </>,
  more: <g fill="currentColor" stroke="none">
    <circle className="setting-action-icon__part setting-action-icon__dot-first" cx="5" cy="12" r="1.5" />
    <circle className="setting-action-icon__part setting-action-icon__dot-center" cx="12" cy="12" r="1.5" />
    <circle className="setting-action-icon__part setting-action-icon__dot-last" cx="19" cy="12" r="1.5" />
  </g>,
  create: <>
    <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6ZM14 3v6h6" />
    <path className="setting-action-icon__part setting-action-icon__create-plus" d="M12 12v6M9 15h6" />
  </>,
  note: <>
    <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6ZM14 3v6h6M8 13h8M8 17h5" />
  </>,
  graph: <>
    <path className="setting-action-icon__part setting-action-icon__quiet" d="M8.5 6h7M7.2 8.2l3.6 7.6M16.8 8.2l-3.6 7.6" />
    <circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" />
  </>,
  agent: <>
    <path d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4v-4a2 2 0 0 1-1-2V6a2 2 0 0 1 2-2Z" />
    <path className="setting-action-icon__part setting-action-icon__agent-spark" d="m10 7 .9 2.6 2.6.9-2.6.9L10 14l-.9-2.6-2.6-.9 2.6-.9L10 7Z" />
    <path className="setting-action-icon__part setting-action-icon__quiet" d="M15 9h2M15 12h2" />
  </>,
  archive: <>
    <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4" />
    <path className="setting-action-icon__part setting-action-icon__archive-lid" d="M3 3h18v5H3z" />
  </>,
  pin: <g className="setting-action-icon__pin">
    <path d="M7 3h10v3h-1v4l3 4v2H5v-2l3-4V6H7V3ZM12 16v5" />
  </g>,
  close: <path className="setting-action-icon__part setting-action-icon__quiet" d="m6 6 12 12M18 6 6 18" />,
  copy: <>
    <path d="M7 16H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v2" />
    <rect className="setting-action-icon__part setting-action-icon__copy-sheet" x="8" y="8" width="13" height="13" rx="2" />
  </>,
  plus: <path className="setting-action-icon__part setting-action-icon__quiet" d="M12 5v14M5 12h14" />,
  minus: <path className="setting-action-icon__part setting-action-icon__quiet" d="M5 12h14" />,
  fit: <path className="setting-action-icon__part setting-action-icon__fit-corners" d="M9 4H4v5M15 4h5v5M4 15v5h5M20 15v5h-5" />,
}

export default function SettingActionIcon({ name, size = 20, className = '' }: SettingActionIconProps) {
  return <svg className={`setting-action-icon setting-action-icon--${name}${className ? ` ${className}` : ''}`} width={size} height={size}
    viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {paths[name]}
  </svg>
}
