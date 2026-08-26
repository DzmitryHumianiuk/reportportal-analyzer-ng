import type { ReactNode } from 'react';

export interface CardProps {
  title: ReactNode;
  /** Pipeline step number rendered as the topaz square before the title. */
  step?: number;
  /** Small grey line on the right of the head. */
  sub?: ReactNode;
  /** Controls on the right of the head (rendered after `sub` when both are set). */
  right?: ReactNode;
  className?: string;
  id?: string;
  children: ReactNode;
}

/** White RP card: head (optional step square + title + sub/right) and a body. */
export function Card({ title, step, sub, right, className, id, children }: CardProps) {
  return (
    <div className={`card${className ? ` ${className}` : ''}`} id={id}>
      <div className="card-head">
        <div className="card-title">
          {step != null ? <span className="step-num">{step}</span> : null}
          {title}
        </div>
        {sub != null ? <div className="card-sub">{sub}</div> : null}
        {right ?? null}
      </div>
      <div className="card-body">{children}</div>
    </div>
  );
}
