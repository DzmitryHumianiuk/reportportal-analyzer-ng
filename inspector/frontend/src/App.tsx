import { Suspense, lazy, type ComponentType, type LazyExoticComponent } from 'react';

import type { ViewName } from './app/hash';
import { useApp } from './app/state';
import { EmptyState } from './components/EmptyState';
import { Loading } from './components/Loading';
import { TabsNav } from './components/TabsNav';
import { ToastHost } from './components/ToastHost';
import { Topbar } from './components/Topbar';
import { ViewErrorBoundary } from './components/ViewErrorBoundary';

const VIEWS: Record<ViewName, LazyExoticComponent<ComponentType>> = {
  journey: lazy(() => import('./views/journey/Journey')),
  signatures: lazy(() => import('./views/signatures/Signatures')),
  drain: lazy(() => import('./views/drain/Drain')),
  modes: lazy(() => import('./views/modes/Modes')),
  groups: lazy(() => import('./views/groups/Groups')),
  loop: lazy(() => import('./views/loop/Loop')),
  llm: lazy(() => import('./views/llm/Llm')),
  rubric: lazy(() => import('./views/rubric/Rubric')),
};

function ViewArea() {
  const { boot, view, project, refreshTick } = useApp();

  if (boot.status === 'loading') return <Loading />;

  if (boot.status === 'empty') {
    return (
      <EmptyState
        icon="🗄️"
        title="No projects found"
        body="The analyzer database has no project rows. Ingest at least one launch through the analyzer, then reload."
        tag="analyzer.project"
      />
    );
  }

  if (boot.status === 'error') {
    return (
      <EmptyState
        icon="🚫"
        title="Cannot reach the inspector API"
        body={boot.message}
        tag="GET /api/projects"
      />
    );
  }

  const View = VIEWS[view];
  // A failed view must get a fresh try on a view switch, a project switch and
  // every refresh — otherwise the error state is a dead end.
  return (
    <ViewErrorBoundary resetKey={`${view}:${project}:${refreshTick}`}>
      <Suspense fallback={<Loading />}>
        <View />
      </Suspense>
    </ViewErrorBoundary>
  );
}

export default function App() {
  const { view, setView } = useApp();
  return (
    <>
      <Topbar />
      <TabsNav view={view} onChange={setView} />
      <main id="view" className="view">
        <ViewArea />
      </main>
      <ToastHost />
    </>
  );
}
