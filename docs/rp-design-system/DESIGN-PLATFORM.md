# ReportPortal New Design Language — Design Platform for the Inspector

Extracted at the source from the two repos that carry RP's new design language
(the one used by TMS pages — Test Case Library, Test Plans — and the reworked
Project Settings):

| Repo | Ref | Role |
|---|---|---|
| `github.com/reportportal/ui-kit` | `main` (npm `@reportportal/ui-kit`) | The design system itself: tokens, themes, components. Figma source is linked in code: "RP Design System 6" (`src/assets/styles/themes/base.scss:9`) |
| `github.com/reportportal/service-ui` | `develop` | Consumer. New-design areas import `@reportportal/ui-kit` (125+ files under `pages/inside/testCaseLibraryPage`, `testPlansPage`, `projectSettingsPageContainer`) |

Provenance paths below are relative to each repo root. Companion file:
[`tokens.css`](./tokens.css) — drop-in `:root` custom properties (`--rp-*`).

The new RP UI is **light-first**. A dark theme exists in ui-kit
(`src/assets/styles/themes/dark.scss`, class `rp-ui-kit-theme-dark`) but the
light theme is the default (`base.scss` applies `@include light-theme` on
`:root`) and is the only one with complete UX sign-off (dark carries
`TODO: Discuss with UX team` markers). Build the Inspector light; keep the
dark base tokens (`--rp-ui-base-dark-*`) in reserve if a dark mode is ever
wanted.

---

## 1. Color tokens

All from `ui-kit/src/assets/styles/themes/base.scss` unless noted.

### 1.1 Surfaces

| Token | Value | Use |
|---|---|---|
| `bg-000` | `#ffffff` | Cards, table rows, fields, page header |
| `bg-100` | `#f7f7f8` | Page background, modal background |
| `bg-200` | `#eceff4` | Chip background, selected/segmented-control track |

### 1.2 Brand — "topaz" teal

| Token | Value | Use |
|---|---|---|
| `topaz` | `#00829b` | Primary buttons, links, checked controls |
| `topaz-hover` | `#009dbb` | Hover |
| `topaz-pressed` | `#00758c` | Active/pressed |
| `topaz-focused` | `#00b0d1` | **Every** focus ring (even on error buttons) |
| `topaz-100` | `#e5f5f8` | Selected dropdown item background |

### 1.3 Ink and neutrals (the "e" ramp)

| Token | Value | Use |
|---|---|---|
| `almost-black` | `#3f3f3f` | Primary text (never pure black) |
| `charcoal` | `#464547` | Menu text |
| `e-100` | `#e3e7ec` | Dividers, table-header bottom rule |
| `e-200` | `#c1c7d0` | Field borders, unchecked checkbox border |
| `e-300` | `#a2aab5` | Icons, disabled, toggle-off track |
| `e-400` | `#8d95a1` | Secondary text, table header text |
| `e-91` | `#e9e9e9` | Rarely used light neutral |

### 1.4 Feedback

| Token | Value | Use |
|---|---|---|
| `error` | `#dc5959` | Danger buttons, invalid field border |
| `error-hover` / `error-pressed` | `#f24a4a` / `#c54141` | States |
| `sm-error` | `#db3549` | Alert (toast) error background |
| `sm-error-line-100/200` | `#fccbcb` / `#ffc0bd` | Validation error strips |
| `sm-warning` | `#d78706` | Warning text/icon |
| `sm-warning-line-100/200` | `#fceecb` / `#fbe7b6` | Warning strips |
| `sm-info-line-100` | `#ced3db` | Info strip |
| `yellow-800` | `#ffc208` | Warning chip border, AB group |
| `a-green-800` / `a-red-700` | `#1c8b50` / `#c33c3d` | "Passed"/"Failed" execution buttons (`light.scss:46-47`) |
| `status-passed` | `#3aa76d` | Passed status, success alert bg |
| `status-skipped` | `#b8b8b8` | Skipped status |

### 1.5 Domain colors

Defect-type **group** fallbacks (`base.scss:101-104`) — see §4 for why these
are only fallbacks:

| Group | Value |
|---|---|
| Product Bug | `#d32f2f` |
| Automation Bug | `#ffc208` |
| System Issue | `#3e7be6` |
| No Defect | `#76839b` |

TMS priority icons (`base.scss:46-49`): high `#dc5959`, medium `#ffbc6c`,
low `#3e7be6`, unspecified `#c1c7d0`.

Attribute tags (`base.scss:97-100`): key `#6f4599` on `#dac3e6`, value
`#394db6` on `#ced8fc`.

### 1.6 Elevation & overlays

| Token | Value | Use |
|---|---|---|
| shadow tint | `rgb(55, 67, 98)` — blue-grey, never black | `base.scss:109` |
| `shadow` | `0 1px 3px rgba(55,67,98,0.1)` | Resting cards |
| `shadow-hover` | `0 1px 3px rgba(55,67,98,0.2)` | **Table rows** (yes, resting rows use the hover shadow — `table.module.scss:146`) |
| `shadow-secondary` | `0 8px 40px rgba(0,0,0,0.15)` | Modals, popovers, dropdown menus |
| tooltip bg | `rgba(34,34,34,0.91)` | Dark tooltip on light UI |
| overlay | `rgba(141,149,161,0.35)` | Modal scrim |
| overlay-light-cyan | `rgba(208,240,241,0.7)` | Alternate scrim |

## 2. Typography

Provenance: `ui-kit/src/assets/styles/themes/base.scss:130-138`,
`variables/typography.scss`, `mixins/font-scale.scss`, `fonts/*.scss`.

- **Body (everything):** `Roboto, Arial, Helvetica, sans-serif` — self-hosted
  woff2, weights 300/400/500/700 + italics.
- **Headings:** `OpenSans, Segoe UI, Tahoma, sans-serif` — weights 300–800.
  In practice most new pages use Roboto even for the 20px page title
  (`testCaseLibraryPage.scss:42` uses `$FONT-REGULAR` = Roboto).
- **Weights:** thin 100 → bold 700; the workhorses are regular 400
  (body/cells), medium 500 (buttons, labels, chips, primary cells),
  semi-bold 600 (alert titles, defect pills).

Type scale (`font-scale.scss` — exact px pairs):

| Name | Size / line-height | Where used |
|---|---|---|
| s-small | 11 / 16 | Table headers, tooltips, help/error text under fields |
| x-small, small | 12 / 18 | Dense secondary text |
| **base** | **13 / 20** | **Default: body, buttons, inputs, table cells, chips** |
| medium | 14 / 24 | — |
| x2-medium | 15 / 24 | Alert titles (semi-bold) |
| x3-medium | 17 / 24 | Section headings |
| x4-medium | 20 / 31 | Page titles |
| large | 32 / 42 | Hero numbers |

Note the tiny default: RP's new language is a **13px UI**. Do not ship 14-16px
body text if you want it to read as RP.

## 3. Spacing, radii, metrics

No named spacing token file exists; the scale is a consistent 4px grid
observed across ui-kit and the new service-ui pages:
`2, 4, 8, 12, 16, 24, 32, 40`. Signature applications:

- Page content padding: `32px 32px 60px` (`projectSettingsPageContainer.scss`, `testCaseLibraryPage.scss`)
- Page header: `padding: 16px 32px 12px` + `border-bottom: 1px solid #e3e7ec` on white (`testCaseLibraryPage.scss:27-30`)
- Table row gap `4px`; cell padding-x `16px`; input padding `7px 12px`; button padding `7px 16px`
- Settings content column max-width `640px` (`projectSettingsPageContainer/content/layout/layout.scss`)
- Max page width `1600px` (`base.scss:127`)

Radii (each size has a meaning — don't flatten to one value):

| Radius | Applies to | Provenance |
|---|---|---|
| `3px` | Buttons, inputs, dropdowns, checkboxes, tags | `button.module.scss:11`, `fieldText.module.scss`, `checkbox` |
| `4px` | Table-row cards, segmented-control active item, defect color marker | `table.module.scss:148`, `segmentedControl`, `defectTypeRow.scss` |
| `6px` | Chips, segmented-control container | `chip.module.scss:22` |
| `8px` | Tooltips, popovers, dropdown menus | `tooltip.module.scss`, `popover.module.scss:19` |
| `16px` | Modals, system alerts (toasts) | `modal.module.scss`, `systemAlert.module.scss` |
| `100px` | Defect-type pill | `defectTypeItem.scss:27` |

Control metrics: buttons/inputs/dropdowns **36px** tall; chips 28px; icons
16×16; table header 32px; table rows 44 (small) / 64 (default) / 80 (large).

## 4. Defect-type badges — how RP actually colors them

**Defect colors are NOT palette tokens.** Each project defines its defect
types in project config; every `issue_type` row carries `hex_color`,
`long_name`, `abbreviation` (locator like `pb001`). The UI reads the color
from config and inlines it:

- `service-ui/app/src/pages/inside/projectSettingsPageContainer/content/defectTypes/defectTypeRow/defectTypeRow.jsx:141` —
  `<span className={cx('color-marker')} style={{ backgroundColor: color }} />`
- `service-ui/app/src/pages/inside/common/defectTypeItem/defectTypeItem.jsx` —
  pill badge: colored 12px circle (`backgroundColor: defectType.color`) +
  `longName` text in neutral ink.

The palette's `--rp-group-*` colors (`#d32f2f` etc.) are only **group-level
fallbacks** for widgets that aggregate whole groups. The Inspector already
fetches `hex_color`/`long_name`/`abbreviation` from RP's DB — keep using
those, per label, verbatim.

Two sanctioned renderings in the new language:

1. **Color marker + name** (project settings, new): 24×24 swatch,
   `border-radius: 4px`, `background: hex_color`; name beside it at 13px
   medium `#3f3f3f`; abbreviation at 13px `#8d95a1`
   (`defectTypeRow.scss:60-88`).
2. **Pill badge** (lists/grids): height 20px, `border-radius: 100px`,
   `padding: 0 6px`, `border: 1px solid` light neutral, white background,
   inside a **12px colored circle** + name at 13px semi-bold; text stays
   neutral — **color is carried by the dot, not the text or the fill**
   (`defectTypeItem.scss`).

Don't tint pill backgrounds with the defect color at full strength; RP never
does. If you need a filled variant use the hex at low alpha (~12-15%) behind
the dot+text.

## 5. Component recipes (vanilla HTML/CSS)

All recipes assume `tokens.css` is loaded. Global base:

```css
body {
  font-family: var(--rp-font);
  font-size: var(--rp-fs-base);       /* 13px */
  line-height: var(--rp-lh-base);     /* 20px */
  color: var(--rp-almost-black);
  background: var(--rp-bg-100);
}
```

### 5.1 Buttons (`ui-kit/src/components/button/button.module.scss`)

```css
.rp-btn {
  display: inline-flex; align-items: center; justify-content: center;
  height: var(--rp-control-height);            /* 36px */
  padding: 7px 16px;
  border-radius: var(--rp-radius-control);     /* 3px  */
  font: var(--rp-fw-medium) var(--rp-fs-base)/var(--rp-lh-base) var(--rp-font);
  cursor: pointer; box-sizing: border-box;
}
.rp-btn--primary {
  border: 1px solid var(--rp-topaz); background: var(--rp-topaz); color: #fff;
}
.rp-btn--primary:hover  { border-color: var(--rp-topaz-hover);  background: var(--rp-topaz-hover); }
.rp-btn--primary:active { border-color: var(--rp-topaz-pressed); background: var(--rp-topaz-pressed); }
.rp-btn--primary:focus-visible { border: 2px solid var(--rp-topaz-focused); outline: none; }
.rp-btn--ghost {
  border: 1px solid var(--rp-topaz); background: transparent; color: var(--rp-topaz);
}
.rp-btn--danger {
  border: 1px solid var(--rp-error); background: var(--rp-error); color: #fff;
}
.rp-btn--text {          /* link-style button */
  border: 0; background: none; height: auto; padding: 2px;
  color: var(--rp-topaz); line-height: 18px;
}
.rp-btn:disabled { opacity: var(--rp-opacity-disabled); cursor: default; }
```

Variants in the system: `primary`, `ghost`, `danger`, `ghost-danger`, `text`,
`text-danger`. Focus is always a 2px `#00b0d1` border — including on danger.

### 5.2 Chips / tags (`chip.module.scss`)

```css
.rp-chip {
  display: inline-flex; align-items: center; gap: 12px;
  height: var(--rp-chip-height);               /* 28px */
  padding: 4px 8px;
  border-radius: var(--rp-radius-chip);        /* 6px  */
  border: 1px solid transparent;
  background: var(--rp-bg-200);
  color: var(--rp-almost-black);
  font: var(--rp-fw-medium) 13px/20px var(--rp-font);
}
.rp-chip:hover        { background: var(--rp-e-100); }   /* clickable only */
.rp-chip--error       { color: var(--rp-error); border-color: var(--rp-error); }
.rp-chip--warning     { border-color: var(--rp-yellow-800); }
.rp-chip--link        { background: transparent; border-color: var(--rp-e-200); }
```

### 5.3 Badges (defect / status)

```css
/* pill with color dot — defect types (see §4) */
.rp-defect-pill {
  display: inline-flex; align-items: center; gap: 5px;
  height: 20px; padding: 0 6px;
  border: 1px solid var(--rp-e-200);
  border-radius: var(--rp-radius-pill);        /* 100px */
  background: #fff;
  font: var(--rp-fw-semibold) 13px/18px var(--rp-font);
  color: var(--rp-almost-black);
  max-width: 100%; overflow: hidden;
}
.rp-defect-pill .dot {
  width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
  background: var(--dot-color);  /* set from issue_type.hex_color inline */
}
/* square color marker — settings-style listing */
.rp-color-marker { width: 24px; height: 24px; border-radius: 4px; }
```

### 5.4 Tables (`table.module.scss`) — rows are floating cards

The defining pattern of the new language: **no zebra, no row borders** —
each row is a white card with soft blue-grey shadow, 4px gap between rows.

```css
.rp-table         { width: 100%; }
.rp-table-header  {
  display: grid; height: var(--rp-table-header-height); align-items: center;
  background: #fff; border-bottom: 1px solid var(--rp-e-100);
  font: var(--rp-fw-regular) 11px/16px var(--rp-font);   /* s-small */
  color: var(--rp-e-400);
}
.rp-table-body    { display: flex; flex-direction: column; gap: 4px; }
.rp-table-row {
  display: grid; align-items: center;
  min-height: var(--rp-row-height-small);      /* 44 dense / 64 default */
  background: #fff;
  border-radius: var(--rp-radius-row);         /* 4px */
  box-shadow: var(--rp-shadow-hover);          /* 0 1px 3px rgba(55,67,98,.2) */
  padding: 12px 0;                             /* 12 small / 22 default / 30 large */
}
.rp-cell { padding: 0 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rp-cell--primary { font-weight: var(--rp-fw-medium); }
```

### 5.5 Tabs (segmented control, `segmentedControl.module.scss`)

```css
.rp-tabs {
  display: inline-flex; gap: 4px; padding: 2px;
  border-radius: var(--rp-radius-chip);        /* 6px */
  background: var(--rp-bg-200);
}
.rp-tab {
  display: inline-flex; align-items: center; gap: 8px; padding: 8px;
  border: 1px solid transparent; border-radius: 4px;
  background: transparent; color: var(--rp-almost-black);
  font: var(--rp-fw-regular) 13px/20px var(--rp-font);
  cursor: pointer; transition: all .2s ease;
}
.rp-tab:hover:not(.active) { background: var(--rp-e-100); }
.rp-tab.active {
  background: #fff;
  box-shadow: 0 1px 3px rgba(55,67,98,.1);
}
.rp-tab:focus-visible {
  border-color: var(--rp-topaz-focused);
  box-shadow: inset 0 0 0 1px var(--rp-topaz-focused);
}
```

(Page-level nav tabs in settings are route links; the segmented control is
the reusable in-page tab pattern.)

### 5.6 Cards / panels / modals

```css
.rp-card  {        /* generic panel */
  background: #fff; border-radius: var(--rp-radius-row);
  box-shadow: var(--rp-shadow);
}
.rp-modal {        /* modal.module.scss */
  background: var(--rp-bg-100);                /* note: grey, not white */
  border-radius: var(--rp-radius-modal);       /* 16px */
  box-shadow: var(--rp-shadow-secondary);
  padding: 32px 40px;
  width: 480px;                                /* small 320 / large 720 */
}
.rp-popover {      /* popover.module.scss */
  background: #fff; border-radius: 8px; padding: 16px;
  box-shadow: var(--rp-shadow-secondary);
}
```

### 5.7 Tooltips (`tooltip.module.scss`)

```css
.rp-tooltip {
  padding: 16px; border-radius: var(--rp-radius-popover);   /* 8px */
  background: var(--rp-tooltip-bg);            /* rgba(34,34,34,.91) */
  color: #fff;
  font: var(--rp-fw-medium) 11px/16px var(--rp-font);
  word-wrap: break-word; white-space: pre-wrap;
}
```

### 5.8 Toggles (`toggle.module.scss`)

```css
.rp-toggle { position: relative; display: inline-block; width: 32px; height: 20px; }
.rp-toggle input { position: absolute; opacity: 0; }
.rp-toggle .track {
  position: absolute; inset: 0; border-radius: 10px;
  background: var(--rp-e-300); transition: .4s; cursor: pointer;
}
.rp-toggle .track::before {
  content: ''; position: absolute; top: 2px; left: 2px;
  width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: .4s;
}
.rp-toggle input:checked + .track         { background: var(--rp-topaz); }
.rp-toggle input:checked + .track::before { transform: translateX(12px); }
.rp-toggle input:hover:not(:checked) + .track { background: var(--rp-e-400); }
```

### 5.9 Inputs (`fieldText.module.scss`)

```css
.rp-field {
  display: flex; align-items: center;
  height: var(--rp-control-height);            /* 36px */
  padding: 7px 12px;
  border: 1px solid var(--rp-e-200);
  border-radius: var(--rp-radius-control);     /* 3px */
  background: #fff; box-sizing: border-box;
}
.rp-field:hover        { border-color: var(--rp-e-300); }
.rp-field:focus-within {
  border-color: var(--rp-topaz-focused);
  box-shadow: inset 0 0 0 1px var(--rp-topaz-focused);   /* fakes 2px border */
}
.rp-field.invalid      { border-color: var(--rp-error); }
.rp-field input {
  border: 0; outline: 0; background: none; width: 100%;
  font: var(--rp-fw-regular) 13px/20px var(--rp-font); color: var(--rp-almost-black);
}
.rp-field-label {       /* fieldLabel.module.scss */
  display: block; margin-bottom: 4px;
  font: var(--rp-fw-medium) 13px/20px var(--rp-font);
}
.rp-field-help  { font: var(--rp-fw-regular) 11px/16px var(--rp-font); color: var(--rp-e-400); }
.rp-field-error { font: var(--rp-fw-regular) 11px/16px var(--rp-font); color: var(--rp-error); }
```

### 5.10 Status colors / alerts (`systemAlert.module.scss`)

Toast alerts: `border-radius: 16px; padding: 16px; box-shadow:
var(--rp-shadow-secondary)`; background by severity — error `#db3549`,
success `#3aa76d`, info `#76839b`, warning `#ffc208`; title 15/24 semi-bold.
Execution statuses: passed `#3aa76d`, failed `#c33c3d` (button context) /
`#dc5959` (generic error), skipped `#b8b8b8`.

## 6. Do / Don't

**Do**
- Grey page (`#f7f7f8`), white cards — depth via the blue-grey shadow
  `rgba(55,67,98,…)`, never via borders around cards.
- 13px Roboto default; 11px `#8d95a1` for table headers and helper text.
- One accent: topaz `#00829b`. Focus ring is always cyan `#00b0d1`.
- Keep the radius hierarchy: 3 controls → 4 rows → 6 chips → 8 popovers → 16 modals.
- Carry defect color in a dot/swatch; text stays `#3f3f3f`.
- 4px grid; 16px cell padding; 32px page padding.

**Don't**
- Pure black text (`#3f3f3f` max) or pure-black shadows on cards.
- Zebra striping, heavy table grid lines, or bordered rows — the row-card +
  4px gap pattern replaces all of that.
- Blue as primary accent (blue `#3e7be6` is reserved for System Issue / low
  priority semantics).
- Rounded-full buttons or 8px+ radii on inputs — controls are squarish (3px).
- Filling defect badges with the defect hex at 100% — RP uses white pills
  with color dots.
- Mixing font sizes off-scale (no 14px body next to 13px cells).

## 7. Mapping: Inspector's current dark system → RP language

Current source: `analyzer-ng/inspector/static/css/app.css` (`:root`, lines
6-50). The Inspector is dark (`color-scheme: dark`); the RP language is
light. Token-by-token replacement:

| Inspector token (dark) | Current value | RP replacement | Value |
|---|---|---|---|
| `--plane` (page bg) | `#0d0d0d` | `--rp-bg-100` | `#f7f7f8` |
| `--surface-1` (cards) | `#17181a` | `--rp-bg-000` | `#ffffff` |
| `--surface-2` (raised) | `#1e2023` | `--rp-bg-000` + `--rp-shadow` | white card w/ shadow |
| `--surface-3` (highest) | `#26292d` | `--rp-bg-200` | `#eceff4` |
| `--hairline` | `#2c2e31` | `--rp-e-100` | `#e3e7ec` |
| `--hairline-2` | `#383b3f` | `--rp-e-200` | `#c1c7d0` |
| `--ink` | `#f4f5f6` | `--rp-almost-black` | `#3f3f3f` |
| `--ink-2` | `#c3c2b7` | `--rp-charcoal` | `#464547` |
| `--muted` | `#898781` | `--rp-e-400` | `#8d95a1` |
| `--accent` | `#3987e5` (blue) | `--rp-topaz` | `#00829b` — **blue frees up for System Issue** |
| `--accent-soft` | `rgba(57,135,229,.14)` | `--rp-topaz-100` | `#e5f5f8` |
| `--lbl-si` | `#3987e5` | `--rp-group-si` fallback | `#3e7be6` — but prefer project `hex_color` |
| `--lbl-nd` | `#34a853` | `--rp-group-nd` fallback | `#76839b` — prefer project `hex_color` |
| `--lbl-pb` | `#d55181` | `--rp-group-pb` fallback | `#d32f2f` — prefer project `hex_color` |
| `--lbl-ab` | `#c98500` | `--rp-group-ab` fallback | `#ffc208` — prefer project `hex_color` |
| `--lbl-ti` | `#898781` | keep TI from config | RP fallback TI is `#00829b` (topaz — `service-ui/app/src/common/constants/colors.js:26`) |
| `--good` | `#0ca30c` | `--rp-status-passed` | `#3aa76d` |
| `--warning` | `#fab219` | `--rp-sm-warning` | `#d78706` (text) / `#ffc208` (fill) |
| `--serious` | `#ec835a` | `--rp-yellow-700` or `--rp-error` | `#d4a002` / `#dc5959` |
| `--critical` | `#d03b3b` | `--rp-error` / `--rp-sm-error` | `#dc5959` / `#db3549` |
| `--band-abstain` | `#5a5d61` | `--rp-e-300` | `#a2aab5` |
| `--band-suggest` | `#c98500` | `--rp-yellow-700` | `#d4a002` |
| `--band-auto` | `#34a853` | `--rp-status-passed` | `#3aa76d` |
| `--radius` (12px) | `12px` | `--rp-radius-row` for cards | `4px` (or 8px for popovers) |
| `--radius-s` (8px) | `8px` | `--rp-radius-control` | `3px` |
| `--shadow` | black-based | `--rp-shadow` / `--rp-shadow-hover` | blue-grey `rgba(55,67,98,…)` |
| `--sans` | system-ui stack | `--rp-font` | Roboto stack |
| `--mono` | JetBrains Mono stack | keep as-is | RP has no mono token; logs/IDs stay mono |
| body 14px | `font-size: 14px` | `--rp-fs-base` | **13px / 20px** |
| bg radial-gradient glow | blue radial on `--plane` | delete | flat `#f7f7f8` |

**Defect badges in the Inspector:** keep sourcing `hex_color`, `long_name`,
`abbreviation` from RP's project config in the DB (the Inspector already
fetches these). Render per §4: white pill, 1px `#c1c7d0` border, radius
100px, 12px dot filled with `hex_color`, name in 13px semi-bold `#3f3f3f`.
The `--lbl-*` static palette should survive only as a last-resort fallback
when a locator is missing from config — and if kept, swap its values for the
RP group colors above.

## 8. Dark theme (reserve)

ui-kit ships dark base tokens (`base.scss:59-83`): bg `#101010`, surfaces
`#141414`/`#1a1a1a`/`#222222`, topaz remapped to `#1a9cb0` (hover `#1cb0c7`,
text `#3abcd0`), neutral ramp `#e8e8e8 → #222222`. If the Inspector ever
needs to keep a dark mode, remap the semantic aliases per
`themes/dark.scss` rather than inventing values — but the deliverable
direction is light-first, matching RP's shipped product.
