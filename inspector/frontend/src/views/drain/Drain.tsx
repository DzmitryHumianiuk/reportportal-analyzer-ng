// Drain3 Explorer — the searchable log_template mirror next to a token-prefix
// icicle that approximates Drain's parse tree.

import { FieldText, SearchIcon, Table } from '@reportportal/ui-kit';
// The row/column types are only published on the per-component entry, not on
// the package root. Type-only import, so no second module instance is pulled in.
import type { RowData } from '@reportportal/ui-kit/table';
import { useEffect, useRef, useState } from 'react';

import { api } from '../../app/api';
import { useApp } from '../../app/state';
import type { TemplatesResponse } from '../../app/types';
import { Card, EmptyState, HighlightedPattern, Loading } from '../../components';
import { fmt, shortTime } from '../../lib/format';
import { Icicle } from './Icicle';
import './drain.css';

const DEBOUNCE_MS = 250;

/**
 * The filter text is module state, exactly as in the original view: it is not
 * part of the permalink, but it does survive leaving and re-entering the tab.
 */
let lastQuery = '';

export default function Drain(): JSX.Element {
  const { project, refreshTick } = useApp();
  const [text, setText] = useState(lastQuery);
  const [query, setQuery] = useState(lastQuery);
  const [data, setData] = useState<TemplatesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<Error | null>(null);

  // Which project / refresh the data on screen belongs to. Switching project or
  // hitting refresh must drop it, the way the original view did: the shell
  // rebuilt the whole tab from scratch on both. Typing in the search box does
  // not, also as before — the tree and the count stay put while the table
  // reloads.
  const shownFor = useRef('');

  // Debounced hand-off from the input to the request.
  useEffect(() => {
    if (text === query) return undefined;
    const timer = setTimeout(() => {
      lastQuery = text;
      setQuery(text);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text, query]);

  useEffect(() => {
    if (project == null) return undefined;
    let cancelled = false;
    const scope = `${project}:${refreshTick}`;
    if (shownFor.current !== scope) {
      shownFor.current = scope;
      setData(null);
    }
    setLoading(true);
    api
      .templates(project, query || null)
      .then((res) => {
        if (!cancelled) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setFailure(err instanceof Error ? err : new Error(String(err)));
      });
    return () => {
      cancelled = true;
    };
  }, [project, query, refreshTick]);

  // Hand fetch failures to the shell's view error boundary.
  if (failure) throw failure;

  const templates = data?.templates ?? [];
  const templatesSub = data ? `${data.count} template(s)` : 'log_template mirror';

  const rows: RowData[] = templates.map((t) => ({
    id: t.template_id,
    pattern: {
      content: t.pattern ?? '',
      component: (
        <div className="pattern drain-pattern">
          <HighlightedPattern pattern={t.pattern ?? null} />
        </div>
      ),
    },
    templateId: {
      content: t.template_id,
      component: <span className="mono">#{t.template_id}</span>,
    },
    tok: t.token_count ?? '',
    matches: {
      content: t.match_count ?? 0,
      component: <span className="mono">{fmt(t.match_count)}</span>,
    },
    lastSeen: {
      content: t.last_seen ?? '',
      component: <span className="muted mono drain-lastseen">{shortTime(t.last_seen)}</span>,
    },
  }));

  return (
    <>
      <div className="picker-row">
        <FieldText
          className="drain-search"
          label="Search templates"
          placeholder="Filter patterns (ILIKE)…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          startIcon={<SearchIcon />}
          clearable
          onClear={() => setText('')}
          defaultWidth={false}
        />
      </div>

      <div className="grid grid-2">
        <Card
          title="Parse tree (icicle)"
          sub="grouped by leading masked tokens — approximates Drain’s tree"
        >
          {data == null ? (
            <Loading />
          ) : templates.length === 0 ? (
            <EmptyState
              icon="🌳"
              title="Nothing to tree"
              body="No patterns to build a parse tree from."
              tag="analyzer.log_template"
            />
          ) : (
            <Icicle templates={templates} />
          )}
        </Card>

        <Card title="Templates" sub={templatesSub}>
          {loading ? (
            <Loading />
          ) : templates.length === 0 ? (
            <EmptyState
              icon="🌵"
              title="No templates"
              body={
                query
                  ? 'No pattern matches your filter.'
                  : 'Drain3 has not mined any templates for this project yet — templates appear as error logs are parsed.'
              }
              tag="analyzer.log_template"
            />
          ) : (
            <div className="drain-table-wrap">
              {/*
                Column order differs from the original table on purpose: it read
                `id | pattern | tok | matches | last seen`, this one reads
                `pattern | id | …`. The ui-kit Table always draws the primary
                column first, and only that column takes the leftover width. The
                pattern is the one value that needs room to grow, so it takes the
                primary slot and the id moves in behind it.
              */}
              <Table
                data={rows}
                primaryColumn={{ key: 'pattern', header: 'pattern' }}
                fixedColumns={[
                  { key: 'templateId', header: 'id', width: 96 },
                  { key: 'tok', header: 'tok', width: 72, align: 'right' },
                  { key: 'matches', header: 'matches', width: 104, align: 'right' },
                  { key: 'lastSeen', header: 'last seen', width: 150 },
                ]}
              />
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
