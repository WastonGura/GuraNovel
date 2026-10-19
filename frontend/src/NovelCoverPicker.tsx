import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { uploadNovelCover } from './api/client'
import CoverImage from './CoverImage'
import PickerDialog, { type PickerDialogHandle } from './PickerDialog'
import { novelCoverPresets, novelCoverSource, novelDefaultCover } from './novelCover'

export default function NovelCoverPicker({ source, onChange, disabled }: { source: string; onChange: (cover: string) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)
  const request = useRef(0)
  const picker = useRef<PickerDialogHandle>(null)
  const upload = useRef<AbortController | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const coverFrame = useRef<HTMLButtonElement>(null)
  const [frameRatio, setFrameRatio] = useState(.75)
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) setFrameRatio(width / height)
    })
    observer.observe(coverFrame.current!)
    return () => observer.disconnect()
  }, [])
  useEffect(() => () => { request.current++; upload.current?.abort() }, [])
  function cancelPending() { request.current++; upload.current?.abort(); setChecking(false); setUploading(false) }
  function choose(value: string) { onChange(value); picker.current?.close() }
  async function uploadImage(file: File) {
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { setError('请选择 PNG、JPEG 或 WebP 图片。'); return }
    if (file.size > 5 * 1024 * 1024) { setError('图片不能超过 5 MB。'); return }
    cancelPending()
    const attempt = ++request.current
    upload.current = new AbortController()
    setUploading(true); setError('')
    try {
      const cover = await uploadNovelCover(file, upload.current.signal)
      if (attempt === request.current) choose(cover)
    } catch { if (attempt === request.current) setError('图片上传失败，原封面未改动。请重试。') }
    finally { if (attempt === request.current) setUploading(false) }
  }
  async function applyImageUrl() {
    const value = url.trim()
    if (novelCoverSource(value, '') === '') { setError('请填写 http 或 https 图片地址。'); return }
    const attempt = ++request.current
    setChecking(true); setError('')
    const image = new Image()
    image.src = value
    try {
      await image.decode()
      if (attempt === request.current) choose(value)
    } catch { if (attempt === request.current) setError('图片无法加载，请检查地址后重试。') }
    finally { if (attempt === request.current) setChecking(false) }
  }
  return <>
    <button ref={coverFrame} className="novel-cover-preview novel-cover-change" type="button" aria-label="更换封面" aria-haspopup="dialog" disabled={disabled} onClick={() => { setUrl(''); setError(''); setOpen(true) }}>
      <CoverImage source={source} mode="detail" priority widthHint={440} frameRatio={frameRatio} /><span>更换封面</span>
    </button>
    {open && <PickerDialog ref={picker} onClosing={cancelPending} title="更换封面" className="cover-picker-dialog" initialFocus='.cover-picker-default[aria-pressed="true"], .cover-picker-grid button[aria-pressed="true"]' onClose={() => setOpen(false)}>
      <button className="cover-picker-default" type="button" aria-label="默认封面" aria-pressed={source === novelDefaultCover} onClick={() => choose(novelDefaultCover)}><span className="cover-default-swatch" /><span>默认封面</span><span aria-hidden="true">{source === novelDefaultCover ? '✓' : ''}</span></button>
      <div className="cover-picker-grid" style={{ '--cover-preview-ratio': frameRatio } as CSSProperties}>{novelCoverPresets.map((value, index) => <button type="button" aria-label={`封面 ${index + 1}`} aria-pressed={source === novelCoverSource(value)} key={value} onClick={() => choose(value)}>
        <CoverImage source={novelCoverSource(value)} mode="picker" widthHint={130} frameRatio={frameRatio} /><span aria-hidden="true">{source === novelCoverSource(value) ? '✓' : ''}</span>
      </button>)}</div>
      <div className="cover-picker-upload"><input className="cover-upload-input" ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" aria-label="上传本地封面" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void uploadImage(file) }} />
        <button type="button" disabled={uploading || checking} onClick={() => fileInput.current?.click()}>{uploading ? '正在上传…' : '上传本地图片'}</button><span>PNG、JPEG、WebP · 最大 5 MB</span>
      </div>
      <form className="cover-picker-url" onSubmit={event => { event.preventDefault(); event.stopPropagation(); void applyImageUrl() }}>
        <label htmlFor="novel-cover-url">图片链接</label><div><input id="novel-cover-url" type="url" aria-label="封面图片链接" placeholder="https://…" required value={url} onChange={event => { request.current++; setChecking(false); setError(''); setUrl(event.target.value) }} /><button type="submit" disabled={checking || uploading}>{checking ? '检查图片…' : '使用图片'}</button></div>
      </form>
      {error && <p className="cover-picker-error" role="alert">{error}</p>}
    </PickerDialog>}
  </>
}
