import { useEffect, useRef, useState, type ReactNode } from 'react'
import PickerDialog, { type PickerDialogHandle } from './PickerDialog'
import SettingActionIcon from './SettingActionIcon'
import './SettingLifecycle.css'

export default function SettingArchiveDialog({ title, readOnly, impact, impactReady, onArchive, onClose }: {
  title: string; readOnly: boolean; impact: ReactNode; impactReady: boolean; onArchive: () => Promise<boolean>; onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const mounted = useRef(false)
  const dialog = useRef<PickerDialogHandle>(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  async function archive() {
    if (pending.current || readOnly || !impactReady) return
    pending.current = true; setBusy(true); setError('')
    try {
      if (await onArchive()) { if (mounted.current) dialog.current?.close() }
      else if (mounted.current) setError('归档未完成，当前文稿已保留。')
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : '归档结果未能确认，请重试核对。')
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }
  return <PickerDialog ref={dialog} title="归档设定集" closeIcon={<SettingActionIcon name="close" />} className="setting-archive-dialog" initialFocus="[data-archive-cancel]" onClose={onClose}>
    <p className="setting-archive-context">{title}</p>
    <h3 className="setting-archive-heading">归档这个设定集？</h3>
    <p>归档后，条目与历史版本仍可查看，新增、编辑和恢复将停止。</p>
    <p>关联小说和已写章节会保留。后续创作需要使用可用的设定集，归档不会改写旧章节。</p>
    {impact}
    <p className="setting-archive-note">确认前会先同步当前所有条目；未能保存时会保留草稿并暂停归档。</p>
    {error && <p className="setting-lifecycle-error" role="alert">{error}</p>}
    <footer className="setting-archive-footer"><button type="button" data-archive-cancel disabled={busy} onClick={() => dialog.current?.close()}>取消</button><button type="button" className="setting-lifecycle-primary" disabled={busy || readOnly || !impactReady} onClick={() => { void archive() }}>{busy ? '正在归档…' : '确认归档'}</button></footer>
  </PickerDialog>
}
