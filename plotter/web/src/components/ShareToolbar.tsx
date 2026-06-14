import html2canvas from 'html2canvas'
import { useState } from 'react'

import type { Language } from '../i18n'
import { useAppStore } from '../store'

type ShareToolbarProps = {
  targetSelector?: string
  filenamePrefix?: string
}

const copy = {
  en: {
    screenshot: 'Download screenshot',
    mosaicOn: 'Mosaic on',
    mosaicOff: 'Mosaic off',
    busy: 'Capturing...',
    failed: 'Screenshot failed',
  },
  'zh-Hant': {
    screenshot: '下載截圖',
    mosaicOn: 'Mosaic 已開',
    mosaicOff: 'Mosaic 關閉',
    busy: '截圖中...',
    failed: '截圖失敗',
  },
} as const

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, '-')
}

async function downloadElementScreenshot(selector: string, filename: string) {
  const target = document.querySelector<HTMLElement>(selector)
  if (!target) {
    throw new Error(`Screenshot target not found: ${selector}`)
  }
  const canvas = await html2canvas(target, {
    backgroundColor: '#0b1018',
    scale: Math.min(2, window.devicePixelRatio || 1),
    useCORS: true,
    logging: false,
    windowWidth: Math.max(document.documentElement.scrollWidth, target.scrollWidth),
    windowHeight: Math.max(document.documentElement.scrollHeight, target.scrollHeight),
  })
  const link = document.createElement('a')
  link.download = filename
  link.href = canvas.toDataURL('image/png')
  link.click()
}

export function ShareToolbar({
  targetSelector = '[data-share-capture-root]',
  filenamePrefix = 'lo2cin4bt',
}: ShareToolbarProps) {
  const language = useAppStore((state) => state.language)
  const shareMosaicMode = useAppStore((state) => state.shareMosaicMode)
  const setShareMosaicMode = useAppStore((state) => state.setShareMosaicMode)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const labels = copy[language as Language]

  return (
    <div className="share-toolbar" data-html2canvas-ignore="true">
      <button
        type="button"
        className={`inline-action-button inline-action-button-compact ${shareMosaicMode ? 'active' : ''}`}
        onClick={() => setShareMosaicMode(!shareMosaicMode)}
      >
        {shareMosaicMode ? labels.mosaicOn : labels.mosaicOff}
      </button>
      <button
        type="button"
        className="inline-action-button inline-action-button-compact"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError('')
          try {
            await downloadElementScreenshot(targetSelector, `${filenamePrefix}-${timestamp()}.png`)
          } catch {
            setError(labels.failed)
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? labels.busy : labels.screenshot}
      </button>
      {error ? <span className="share-toolbar-error">{error}</span> : null}
    </div>
  )
}
