// Item Journey — the main view. Picker (launch → item) on the left, the
// pipeline stepper and its five stage cards on the right: signature → grouping
// → matching → decision → feedback.

import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '../../app/api';
import { useApp } from '../../app/state';
import type { Item, JourneyResponse, Launch } from '../../app/types';
import { Card, ChipLink, DefectBadge, EmptyState, Loading, RpIcon } from '../../components';
import { setDefects } from '../../lib/labels';
import { DecisionCard } from './DecisionCard';
import { FeedbackCard } from './FeedbackCard';
import { GroupingCard } from './GroupingCard';
import { MatchingCard } from './MatchingCard';
import { SignatureCard } from './SignatureCard';
import { pulse } from './motion';
import './journey.css';

const BAND_NAME: Record<string, string> = {
  auto: 'Auto‑apply',
  suggest: 'Suggest',
  abstain: 'Abstain',
};

function bandName(b?: string | null): string {
  return (b && BAND_NAME[b]) || String(b);
}

const CARD_IDS = ['j-signature', 'j-grouping', 'j-matching', 'j-decision', 'j-feedback'];

// --------------------------------------------------------------------------- //
// Detail pane
// --------------------------------------------------------------------------- //

function JourneyDetail({
  d,
  onNavigate,
}: {
  d: JourneyResponse;
  onNavigate: (itemId: number) => void;
}) {
  const [active, setActive] = useState(0);

  const it = d.item;
  const stages: Array<[string, string]> = [
    ['Signature', d.signature ? d.signature.exc_text || 'built' : 'none'],
    ['Grouping', d.grouping ? `group ${d.grouping.group_id}` : 'none'],
    ['Matching', (d.matching && d.matching.stage_label) || '—'],
    ['Decision', d.decision ? bandName(d.decision.band) : 'no suggestion'],
    ['Feedback', `${(d.feedback || []).length} event(s)`],
  ];

  const focus = (i: number) => {
    setActive(i);
    const el = document.getElementById(CARD_IDS[i]);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    pulse(el);
  };

  return (
    <>
      <div className="flex center wrap" style={{ marginBottom: '14px', gap: '8px 16px' }}>
        <h2
          style={{
            margin: 0,
            fontSize: '18px',
            overflowWrap: 'anywhere',
            minWidth: 0,
            flex: '1 1 auto',
          }}
        >
          {it.item_name || `item ${it.item_id}`}{' '}
          {/* Badges live inside the heading so they ride the end of its last line. */}
          <span
            style={{
              display: 'inline-flex',
              gap: '8px',
              verticalAlign: 'middle',
              whiteSpace: 'nowrap',
              marginLeft: '4px',
            }}
          >
            <DefectBadge locator={it.issue_type} group={it.label_group} />
            {it.is_auto_analyzed ? (
              <span className="badge" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>
                <RpIcon name="bolt" size={12} /> auto‑analyzed
              </span>
            ) : null}
          </span>
        </h2>
        <div className="flex gap-8 wrap" style={{ marginLeft: 'auto', justifyContent: 'flex-end' }}>
          <ChipLink label={`item ${it.item_id}`} url={it.ui_url} />
          <ChipLink label={`launch ${it.launch_id}`} url={it.launch_url} />
          <span className="chip">{`${it.log_count} logs`}</span>
          <span className="chip mono">{`tch ${it.test_case_hash ?? '—'}`}</span>
        </div>
      </div>

      <div className="stepper">
        {stages.map(([k, v], i) => (
          <div
            key={k}
            className={`stage-pill${i === active ? ' active' : ''}`}
            onClick={() => focus(i)}
          >
            <div className="s-k">{`${i + 1} · ${k}`}</div>
            <div className="s-v" title={v}>
              {v}
            </div>
            <div className="s-flow" aria-hidden="true">
              <RpIcon name="arrowRight" size={15} />
            </div>
          </div>
        ))}
      </div>

      <div className="grid" style={{ gap: '16px' }}>
        <SignatureCard d={d} />
        <GroupingCard d={d} onNavigate={onNavigate} />
        <MatchingCard d={d} />
        <DecisionCard d={d} />
        <FeedbackCard d={d} />
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- //
// View
// --------------------------------------------------------------------------- //

export default function Journey() {
  const { project, link, patchLink, refreshTick } = useApp();

  const [launches, setLaunches] = useState<Launch[] | null>(null);
  const [launchError, setLaunchError] = useState<Error | null>(null);
  const [launchId, setLaunchId] = useState<number | null>(link.launch ?? null);

  const [items, setItems] = useState<Item[] | null>(null);
  const [itemsError, setItemsError] = useState<Error | null>(null);
  const [itemId, setItemId] = useState<number | null>(link.item ?? null);

  // The id the LINK asked for: if it never resolves to an indexed item, the view
  // says so instead of silently opening the first item.
  const pendingItem = useRef<number | null>(link.item ?? null);
  const [missingItem, setMissingItem] = useState<number | null>(null);

  const [journey, setJourney] = useState<JourneyResponse | null>(null);
  const [journeyError, setJourneyError] = useState<string | null>(null);
  const [journeyLoading, setJourneyLoading] = useState(false);

  // Launch and item ids belong to one project. A project switch nulls
  // link.launch / link.item, but the local copies still name rows of the
  // project we just left. Retire them while rendering: an effect would be too
  // late — the fetch effects below run in the same pass and would ask the new
  // project for the old project's launch and item, which reads as a 404.
  const [idProject, setIdProject] = useState(project);
  if (project !== idProject) {
    setIdProject(project);
    setLaunchId(null);
    setItemId(null);
    setMissingItem(null);
    pendingItem.current = null;
  }

  const selectItem = useCallback(
    (id: number) => {
      setMissingItem(null);
      setItemId(id);
      patchLink({ item: id });
    },
    [patchLink],
  );

  const selectLaunch = useCallback(
    (id: number) => {
      setLaunchId(id);
      patchLink({ launch: id });
    },
    [patchLink],
  );

  // Picking a launch from the list starts a fresh selection. An item id armed by
  // a permalink names an item in the launch it was seeded under, so it must not
  // be tested against another launch's list: that turned a plain launch switch
  // into a false "item X is not in the analyzer's index".
  const pickLaunch = useCallback(
    (id: number) => {
      pendingItem.current = null;
      setMissingItem(null);
      selectLaunch(id);
    },
    [selectLaunch],
  );

  // What the link last asked for. The re-seed below must react to a real link
  // change and nothing else; comparing against the local ids instead would
  // re-arm an item this view has already retired.
  const seenLink = useRef<{ launch: number | null; item: number | null }>({
    launch: link.launch ?? null,
    item: link.item ?? null,
  });

  // A real hashchange (browser back/forward, edited URL) re-seeds the selection.
  // A project switch sets both to null: the local ids name rows of the project
  // we just left, so they go too. Holding on to them would fetch api/items and
  // api/journey for ids the new project does not have and paint a 404 on the
  // way.
  useEffect(() => {
    const next = { launch: link.launch ?? null, item: link.item ?? null };
    const prev = seenLink.current;
    seenLink.current = next;
    if (next.launch !== prev.launch) setLaunchId(next.launch);
    if (next.item !== prev.item) {
      pendingItem.current = next.item;
      setItemId(next.item);
      setMissingItem(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [link.launch, link.item]);

  // ---- launches -----------------------------------------------------------
  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    setLaunches(null);
    setLaunchError(null);
    void (async () => {
      try {
        const { launches: list } = await api.launches(project);
        if (!cancelled) setLaunches(list || []);
      } catch (e) {
        if (!cancelled) setLaunchError(e as Error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, refreshTick]);

  // Restore the permalinked launch when it exists in this project; otherwise
  // fall back to the first launch (honest degradation, never a blank view).
  useEffect(() => {
    if (!launches || !launches.length) return;
    const found = launches.some((l) => l.launch_id === launchId);
    selectLaunch(found && launchId != null ? launchId : launches[0].launch_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [launches]);

  // ---- items --------------------------------------------------------------
  useEffect(() => {
    // No launch means no item list. Clearing keeps the previous project's items
    // from sitting under the new project's launches while they load.
    if (project == null || launchId == null) {
      setItems(null);
      return undefined;
    }
    let cancelled = false;
    setItems(null);
    setItemsError(null);
    void (async () => {
      try {
        const { items: list } = await api.items(project, launchId);
        if (!cancelled) setItems(list || []);
      } catch (e) {
        if (!cancelled) setItemsError(e as Error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, launchId, refreshTick]);

  // Auto-select the permalinked (or first) item. A permalink naming an item the
  // analyzer never indexed gets an honest explanation instead of a silent
  // fall-through to the first item; the hash stays as the link said.
  useEffect(() => {
    if (items == null) return;
    if (!items.length) {
      setItemId(null);
      return;
    }
    const wanted = pendingItem.current;
    pendingItem.current = null;
    const pick = items.find((x) => x.item_id === itemId);
    if (!pick && wanted != null) {
      setItemId(null);
      setMissingItem(wanted);
      return;
    }
    selectItem((pick || items[0]).item_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  // ---- journey ------------------------------------------------------------
  useEffect(() => {
    if (project == null || itemId == null) {
      setJourney(null);
      setJourneyLoading(false);
      return undefined;
    }
    let cancelled = false;
    setJourneyLoading(true);
    setJourneyError(null);
    void (async () => {
      try {
        const data = await api.journey(project, itemId);
        if (cancelled) return;
        // The journey payload carries its own RP name map; install it before
        // the detail renders, so the defect badges resolve real names.
        if (data.rp) setDefects(data.rp.defects);
        setJourney(data);
        setJourneyLoading(false);
      } catch (e) {
        if (cancelled) return;
        setJourney(null);
        setJourneyError(String((e as Error)?.message || e));
        setJourneyLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project, itemId, refreshTick]);

  // Grouping member dots navigate to a co-failing item — same launch, already
  // in this list. Ids outside the list are not navigable and stay inert.
  const navToItem = useCallback(
    (id: number) => {
      if (items && items.some((x) => x.item_id === id)) selectItem(id);
    },
    [items, selectItem],
  );

  // Picker fetch failures are view-level failures: let the shell's error
  // boundary render the honest "Failed to load view" state.
  if (launchError) throw launchError;
  if (itemsError) throw itemsError;

  const noLaunches = launches != null && launches.length === 0;

  let main = (
    <EmptyState
      icon="🧭"
      title="Pick a launch, then an item"
      body="The Item Journey traces one failing test item through every pipeline stage: signature → grouping → matching → decision → feedback."
    />
  );
  if (missingItem != null) {
    main = (
      <EmptyState
        icon="🕳️"
        title={`Item ${missingItem} is not in the analyzer's index`}
        body="No journey exists for it. Most often the failure produced no ERROR logs, so the analyzer had nothing to build a signature from; that is also why its Make Decision modal stays in the plain stock view. It can also mean the item belongs to another launch or has not been indexed yet. Pick an item from the list on the left."
        tag="analyzer.test_item"
      />
    );
  } else if (journeyError) {
    main = <EmptyState icon="🚫" title="Journey failed" body={journeyError} />;
  } else if (journeyLoading) {
    main = <Loading text="Building journey…" />;
  } else if (journey) {
    main = <JourneyDetail key={journey.item.item_id} d={journey} onNavigate={navToItem} />;
  }

  return (
    <div
      className="grid"
      style={{ gridTemplateColumns: '300px minmax(0, 1fr)', alignItems: 'start' }}
    >
      <div className="grid" style={{ gap: '16px' }}>
        <Card title="Launches" sub="pick one">
          {launches == null ? (
            <Loading />
          ) : noLaunches ? (
            <EmptyState
              icon="📭"
              title="No launches"
              body="This project has no test items yet."
              tag="analyzer.test_item"
            />
          ) : (
            <div className="list">
              {launches.map((l) => (
                <div
                  key={l.launch_id}
                  className={`list-item${l.launch_id === launchId ? ' active' : ''}`}
                  onClick={() => pickLaunch(l.launch_id)}
                >
                  <div className="li-main">
                    <div className="li-title">{l.launch_name || `launch ${l.launch_id}`}</div>
                    <div className="li-sub">
                      {`#${l.launch_number ?? '—'} · id ${l.launch_id} · ${l.item_count} items`}
                    </div>
                  </div>
                  <span
                    className="chip"
                    title={`${l.labeled_count} of ${l.item_count} indexed items carry a defect label`}
                  >
                    {`${l.labeled_count}/${l.item_count} labeled`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Failing items" sub="pick one">
          {noLaunches ? (
            <div className="muted" style={{ fontSize: '12px' }}>
              —
            </div>
          ) : items == null ? (
            <Loading />
          ) : !items.length ? (
            <div className="muted">No items in this launch.</div>
          ) : (
            <div className="list">
              {items.map((it) => (
                <div
                  key={it.item_id}
                  className={`list-item${it.is_auto_analyzed ? ' halo-auto' : ''}${
                    it.item_id === itemId ? ' active' : ''
                  }`}
                  onClick={() => selectItem(it.item_id)}
                >
                  <div className="li-main">
                    <div className="li-title">{it.item_name || `item ${it.item_id}`}</div>
                    <div className="li-sub">
                      <ChipLink label={`id ${it.item_id}`} url={it.ui_url} className="" />
                      {` · ${it.exc_text || 'no exc'}`}
                    </div>
                  </div>
                  <DefectBadge abbr locator={it.issue_type} group={it.label_group} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div id="journey-main" style={{ minWidth: 0 }}>
        {main}
      </div>
    </div>
  );
}
