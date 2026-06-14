import { PropsWithChildren, type ReactNode } from 'react'

type Props = PropsWithChildren<{
  title?: string
  subtitle?: string
  subtitlePrivate?: boolean
  actions?: ReactNode
}>

export function SectionCard({ title, subtitle, subtitlePrivate = false, actions, children }: Props) {
  const hasHeader = Boolean(title || subtitle || actions)
  return (
    <section className="section-card">
      {hasHeader ? (
        <div className="section-header">
          <div>
            {title ? <div className="section-title">{title}</div> : null}
            {subtitle ? <div className="section-subtitle" data-private-strategy={subtitlePrivate ? 'identity' : undefined}>{subtitle}</div> : null}
          </div>
          {actions ? <div className="section-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className="section-body">{children}</div>
    </section>
  )
}
