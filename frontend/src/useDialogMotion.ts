import { useCallback, useEffect, useRef, useState, type AnimationEvent } from 'react'

export function useDialogMotion(onClose: () => void, beforeClose?: () => Promise<boolean>, busy = false, exitAnimation = 'creation-dialog-out') {
  const [closing, setClosing] = useState(false)
  const requested = useRef(false)
  const dismissed = useRef(false)
  const callback = useRef(onClose)
  useEffect(() => { callback.current = onClose }, [onClose])
  const finish = useCallback(() => {
    if (dismissed.current) return
    dismissed.current = true
    callback.current()
  }, [])
  async function requestClose() {
    if (busy || requested.current) return
    requested.current = true
    if (beforeClose && !await beforeClose()) { requested.current = false; return }
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) finish()
    else setClosing(true)
  }
  useEffect(() => {
    if (!closing) return
    const timer = window.setTimeout(finish, 400)
    return () => window.clearTimeout(timer)
  }, [closing, finish])
  function onAnimationEnd(event: AnimationEvent<HTMLElement>) {
    if (closing && event.target === event.currentTarget && event.animationName === exitAnimation) finish()
  }
  return { closing, requestClose, onAnimationEnd }
}
