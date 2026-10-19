import { createContext } from 'react'

export const DialogDismissContext = createContext<(() => void) | null>(null)
