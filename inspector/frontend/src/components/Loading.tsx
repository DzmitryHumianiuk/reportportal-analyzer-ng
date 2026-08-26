import { BubblesLoader } from '@reportportal/ui-kit';

export interface LoadingProps {
  text?: string;
}

/** Page/section loader. Copy is the original 'Loading…' unless a view overrides it. */
export function Loading({ text = 'Loading…' }: LoadingProps) {
  return (
    <div className="loading" role="status">
      <BubblesLoader />
      {text}
    </div>
  );
}
