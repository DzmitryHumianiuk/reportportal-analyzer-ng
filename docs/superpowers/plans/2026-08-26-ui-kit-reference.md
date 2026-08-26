# @reportportal/ui-kit 0.0.1-alpha.247 — Practical API Reference (Council Report 2, Fable5)

> Spec appendix for the ui-kit migration plan. Prop names are quoted verbatim from
> the `.d.ts` files in `/Users/Dmitriy_Gumeniuk/IdeaProjects/service-ui/app/node_modules/@reportportal/ui-kit/dist/`.
> Real-world usage taken from `/Users/Dmitriy_Gumeniuk/IdeaProjects/service-ui/app/src/`.
> When unsure about a prop, read the installed `.d.ts` — do not invent props.

---

## 1. Setup contract

**Packaging**: ESM-only (`"type": "module"`, no CJS entry). Exports map:
- `.` → `dist/index.js` (types: `dist/components/index.d.ts`)
- `./style.css` → `dist/style.css` (single monolithic CSS bundle — no per-component CSS)
- `./common` → shared hooks/types/utils
- `./*` → per-component JS entry (`dist/<name>.js`) — JS is code-split, CSS is not.

**Peer deps**: `react: 17.x-18.x`, `react-dom: 17.x-18.x`. Runtime deps bundled in (notable: `downshift@6`, `@floating-ui/react`, `framer-motion@10`, `react-dnd@16`, `react-datepicker@7`, `react-resizable`, `rc-scrollbars`).

**App bootstrap** (exactly what service-ui does):

```jsx
// src/index.jsx (service-ui line 30)
import '@reportportal/ui-kit/style.css';

// routes/pageSwitcher.jsx — top of the page tree
<ThemeProvider>            {/* defaults to 'light' */}
  <Layout>...</Layout>
</ThemeProvider>
```

**ThemeProvider props** (`themeProvider.d.ts`):
```ts
{ children?: ReactNode; theme?: 'light' | 'dark' | string; customThemes?: { [themeKey: string]: string /* class name */ }; className?: string }
```
It renders a wrapper div with a theme class — it is CSS-class based, not React-context-token based. Nestable.

**Fonts**: `dist/style.css` contains `@font-face` and ships TTFs in `dist/fonts/` — `OpenSans` and `Roboto`. Body font is `var(--rp-ui-base-font-family)` (Roboto stack). Importing `style.css` pulls the fonts via bundler asset handling — Vite handles `.ttf` from node_modules out of the box.

---

## 2. Component reference

### Button
`ButtonProps extends ComponentPropsWithRef<'button'>`:
`children?`, `icon?: ReactNode`, `iconPlace?: 'start' | 'end'`, `adjustWidthOn?: 'content' | 'wide-content' | 'parent' | 'min-width'`, `disabled?`, `type?`, `onClick?`, `title?`, `className?`, `variant?: 'primary' | 'ghost' | 'danger' | 'text' | 'ghost-danger' | 'text-danger'`.

```jsx
import { Button, PlusIcon } from '@reportportal/ui-kit';

<Button variant="ghost" icon={<PlusIcon />} onClick={openModal}>
  Add project
</Button>
```

### BaseIconButton
`BaseIconButtonProps extends HTMLAttributes<HTMLButtonElement>`: `children` (required, the icon), `className?`, `disabled?`, `onClick?`. Bare icon-button chrome, no variants.

```jsx
<BaseIconButton onClick={onDelete} aria-label="Delete">
  <DeleteIcon />
</BaseIconButton>
```

### FieldText
`FieldTextProps extends InputHTMLAttributes<HTMLInputElement>` adds:
`value?`, `error?: string`, `touched?: boolean` (error only shows when both set), `label?`, `helpText?`, `classNameHelpText?`, `defaultWidth?: boolean` (**set `defaultWidth={false}` to get 100% width** — service-ui does this almost everywhere), `startIcon?/endIcon?: ReactNode`, `clearable?`, `onClear?: (prevValue?: string) => void`, `isRequired?`, `hasDoubleMessage?`, `type?: 'password' | 'text' | 'email'`, `displayError?`, `maxLengthDisplay?: number`, `collapsible?`, `loading?`, `capsLockMessage?`.

```jsx
<FieldText
  label="Test case name"
  defaultWidth={false}
  isRequired
  value={name}
  onChange={(e) => setName(e.target.value)}
  error={error} touched
/>
```
Also exported: **FieldTextFlex** (textarea variant; extra props: `minHeight`, `maxLength`, `maxLengthDisplay`, `ref`).

### FieldNumber
`onChange: (value: number | string) => void` (**required, value-based — not an event!**), `value?: number | string`, `placeholder?`, `disabled?`, `label?`, `postfix?: string`, `min?`, `max?`, `title?`, `error?`, `onFocus?`. Width auto-sizes in `ch` units (max ~5ch).

### Dropdown (the "Select")
Key props (`dropdown.d.ts`):
`options: DropdownOptionType[]` where `DropdownOptionType = { value: string|boolean|number; label: string; disabled?; hidden?; title?; groupRef?; children?: DropdownOptionType[] }` (nested = grouped options); `value: DropdownValue | DropdownValue[]` (required); `onChange: (value) => void` (required, value-based); `multiSelect?`, `optionAll?`, `isOptionAllVisible?`, `onSelectAll?`, `isMultiSelectWithTags?`, `variant?: 'default' | 'ghost'`, `error?`, `touched?`, `placeholder?`, `label?: ReactNode`, `icon?`, `renderOption?: (props: DropdownOptionProps) => ReactNode`, `footer?: ReactNode | ((closeHandler) => ReactNode)`, `clearable?`/`onClear?`/`clearButtonAriaLabel?`, `formatDisplayedValue?`, `includeGroupValue?`, `noMatchesMessage?`, `menuPortalRoot?: Element` (**needed inside Modal/SidePanel to avoid clipping — pass `document.body`**), `disabledOptionTooltipPortalRoot?`, `mobileDisabled?`, `transparentBackground?`, `isListWidthLimited?`, `notScrollable?`, `toggleButtonClassName?`, `selectListClassName?`.

```jsx
<Dropdown
  label="Status"
  options={[{ value: 'passed', label: 'Passed' }, { value: 'failed', label: 'Failed' }]}
  value={status}
  onChange={setStatus}
/>
```

### Autocompletes (SingleAutocomplete / MultipleAutocomplete)
Generic `<T>`, downshift-based. `SingleAutocompleteProps<T>` core: `options: T[]`, `value: T | null`, `onChange` / `onStateChange` (Downshift signatures), `placeholder`, `onFocus`, `onBlur`, `parseValueToString: (value: T | null) => string`, `error: string`, `createWithoutConfirmation: boolean`, `useFixedPositioning: boolean`. Notables: `async?`, `isDropdownMode?`, `skipOptionCreation?`, `renderOption?`, `getUniqKey?`, `minLength?`, `optionsLimit?`/`limitationText?`, `customEmptyListMessage?`/`customNoMatchesMessage?`/`newItemButtonText?`, `menuPortalRoot?`, `inputProps?: ComponentProps<typeof FieldText>`, `stateReducer?`, `dropdownMatchInputWidth?`, `placement?`. Note: many props are declared non-optional in the .d.ts even though functionally optional — TS consumers must supply defaults.

### Checkbox
`extends HTMLAttributes<HTMLInputElement>`: `value?: boolean` (**checked state is `value`, not `checked`**), `children?` (label), `disabled?`, `className?`, `onChange?: ChangeEventHandler<HTMLInputElement>`, `partiallyChecked?`, `title?`.

```jsx
<Checkbox value={isEnabled} onChange={(e) => setEnabled(e.target.checked)}>
  Enable auto-analysis
</Checkbox>
```

### Toggle
`value: boolean` (required), `title?`, `children?` (label), `disabled?`, `className?`, `onChange?: ChangeEventHandler`.

```jsx
<Toggle value={on} onChange={(e) => setOn(e.target.checked)}>Notifications</Toggle>
```

### Radio / RadioGroup
`RadioOption = { value: string|number; label: string; disabled: boolean }` (disabled is **non-optional** in the type). `RadioProps`: `option: RadioOption`, `value?` (currently selected), `children?`, `onChange?`. `RadioGroupProps = Omit<RadioProps,'option'> & { options: RadioOption[] }`.

### SegmentedControl
`options: { value: string|number; label: string; icon?; disabled?; selected?; className? }[]` — **selection lives on the option (`selected`), there is no top-level `value` prop**; `onChange?: (value) => void`, `fullWidth?`, `ariaLabel?`, `className?`.

```jsx
<SegmentedControl
  ariaLabel="View"
  options={[{ value: 'table', label: 'Table', selected: view === 'table' },
            { value: 'board', label: 'Board', selected: view === 'board' }]}
  onChange={setView}
/>
```

### Table (detail)
From `table/types.d.ts`:

**Columns** — two kinds:
- `primaryColumn: Column | Column[]` where `Column = { key, header }` (`PrimaryColumn` adds `primary`, `width?`, `minWidth?`, `maxWidth?`). Primary column flexes: `minmax(width, 1fr)`.
- `fixedColumns: FixedColumn[]` = `{ key; header; width: string|number (required); align?: 'left'|'center'|'right'; minWidth?; maxWidth? }`.
- Pinning: `pinnedColumnKeys?: string[]` (+ `isHorizontallyScrollable?`).

**Rows** — `data: RowData[]`, `RowData = { id: string|number; [columnKey]: string | number | DetailedCellData | ...; rowConfigs?: { size?: 'small'|'medium'|'large' }; metaData?: Record<string, any> }`. A cell value can be a `DetailedCellData = { content: string|number; component: ReactNode }` — `content` used for sorting, `component` rendered.

**Sorting** — controlled: `sortingDirection?: 'asc'|'desc'|'ASC'|'DESC'`, `sortingColumn?: Column`, `sortableColumns?: string[]`, `onChangeSorting?: (sortConfig?: { key; direction }) => void`, `sortDisabledColumnTooltips?: Record<string, ReactNode>`. Helper `sortTableData(tableData, sortConfig)` exported if you want client-side sorting.

**Row actions** — `renderRowActions?: (metaData?: MetaData) => ReactNode` (rendered in trailing 48px column; receives the row's `metaData`).

**Selection** — `selectable?`, `selectedRowIds?`, `onToggleRowSelection?`, `onToggleAllRowsSelection?`, `disabledRowIds?`, `isCheckboxOutside?`, `isSelectAllCheckboxAlwaysVisible?`, `getRowCheckboxTooltip?`.

**Expansion** — `isRowsExpandable?`, `expandedRowIds?`, `setExpandedRowIds?: Dispatch<SetStateAction<Set<string|number>>>`, `onToggleRowExpansion?`, `onToggleAllRowsExpansion?`, `isAllExpandedByDefault?`, `expandAllTooltip?`.

**Resize** — `isResizable?`, `minColumnWidth?`, `maxColumnWidth?`, `onColumnResize?: (columnKey, width) => void`.

**Layout/scroll** — `isHeaderFixed?`, `externalScrollContainerRef?`, `portalContainer?`, plus many `*ClassName` props.

Real usage (`pages/organization/organizationProjectsPage/projectsListTable/projectsListTable.jsx:138`):
```jsx
<Table
  data={data}
  primaryColumn={{ key: 'name', header: formatMessage(messages.projectName) }}
  fixedColumns={[
    { key: 'usersCount', header: '...', width: 100, align: 'right' },
    { key: 'lastLaunch', header: '...', width: 156 },
  ]}
  sortingDirection={sortingDirection}
  sortingColumn={primaryColumn}
  sortableColumns={[primaryColumn.key]}
  renderRowActions={(metaData) => <ProjectActionMenu details={metaData} />}
  onChangeSorting={onTableColumnSort}
/>
```
Rich cells: `row.testPlanName = { content: nameString, component: <Button ...>{name}</Button> }`.

For row-action menus pair with **ActionMenu** (`actionMenu.d.ts`): `items?: (ActionItem | DividerItem | ReactNode)[]` with `ActionItem = { id?; label; onClick?; hasPermission?; disabled?; className? }`, `trigger?: ReactNode` (defaults to meatball), `placement?`, `shouldUsePortal?`, `ACTION_MENU_DIVIDER` constant.

### Pagination
All required: `activePage`, `totalPages`, `pageSize`, `totalItems`, `pageSizeOptions: number[]`, `changePage: (page) => void`, `changePageSize: (size) => void`. Optional: `captions?: { items?, of?, page?, goTo?, goAction?, perPage? }`, `warningContent?`, `limitExceeded?`, `accentTotalTooltip?`, `className?`.

```jsx
<Pagination activePage={page} totalPages={pageCount} pageSize={limit}
  totalItems={itemCount} pageSizeOptions={[10, 20, 50]}
  changePage={setPage} changePageSize={setLimit} />
```

### Modal
`ModalProps`: `onClose?`, `title?: ReactNode`, `children?`, `okButton?: ExtendedButtonProps` (= `ButtonProps & { tooltipNode?: ReactNode }`), `cancelButton?: ButtonProps`, `footerNode?`, `createFooter?: (closeHandler) => ReactNode`, `withoutFooter?`, `size?: 'default' | 'small' | 'large'`, `overlay?: 'default' | 'light-cyan'`, `allowCloseOutside?`, `scrollable?`, `zIndex?`, `description?`. Sub-parts exported: `ModalContent`, `ModalHeader`, `ModalFooter`. **Modal is always-rendered-means-open**: mount/unmount controls visibility.

```jsx
<Modal
  title="Delete test case"
  okButton={{ children: 'Delete', onClick: onSubmit, variant: 'danger', disabled: isLoading }}
  cancelButton={{ children: 'Cancel' }}
  onClose={hideModal}
>
  Are you sure you want to delete <b>{name}</b>?
</Modal>
```

### Popover
`content: ReactNode` (popover body), `children: ReactNode` (trigger), `placement?: Placement` (floating-ui), `fallbackPlacements?`, `title?`, `strategy?`, `arrowOffset?`, `arrowColor?`, `safeZone?`, `transitionDuration?`, `isCentered?`, `isFocusDisabled?`, `isOpened?` / `setIsOpened?` (controlled mode), `shouldUsePortal?`, `dataAutomationId?`.

### Tooltip
`content`, `children`, `placement?`, `width?`, `minWidth?`, `dynamicWidth?`, `wrapperClassName?`, `wrapperTabIndex?`, `tooltipClassName?`, `contentClassName?`, `arrowColor?`, `safeZone?`, `zIndex?`, `mainAxis?`, `portalRoot?: Element`, `isFloating?`. Pairs with exported hook `useEllipsisTitle`.

```jsx
<Tooltip content="Refresh data" placement="top">
  <BaseIconButton onClick={refresh}><RefreshIcon /></BaseIconButton>
</Tooltip>
```

### SidePanel
`isOpen?`, `onClose?`, `side?: 'left' | 'right'`, `top?: number`, `title?: ReactNode`, `headerComponent?`, `descriptionComponent?`, `contentComponent?: ReactNode` (**content goes in a prop, not children**), `footerComponent?`, `showOverlay?`, `overlay?: 'default' | 'light-cyan'`, `allowCloseOutside?`, `closeButtonAriaLabel?`, `contentClassName?`, `overlayClassName?`.

### Breadcrumbs
`descriptors: BreadcrumbDescriptor[]` = `{ title: string|ReactNode; link?: object|string; onClick?; className? }`; `LinkComponent?` (inject your router Link: `{ to, className, onClick, children }`); `tree?: TreeDescriptor[]`; `isBackButton?`, `isLastClickable?`, `isSingleItemClickable?`, `maxShownDescriptors?`, `titleTailNumChars?`. Also exports `BreadcrumbsProvider`.

### Chip
`children` (required), `variant?: 'default' | 'error' | 'warning' | 'link'`, `link?: string`, `onClick?`, `onRemove?` (shows the x button), `maxWidth?: number`, `title?`, `disabled?`.

### SystemAlert (toast)
`title: string` (required), `onClose: () => void` (required), `type?: SystemAlertType` enum (`'info' | 'success' | 'warning' | 'error'`), `typographyColor?: 'white' | 'black'` enum, `icon?: ReactElement | null`, `duration?: number` (auto-dismiss), `dataAutomationId?`. Positioning/stacking is yours.

```jsx
<SystemAlert type={SystemAlertType.SUCCESS} title="Saved" duration={4000} onClose={dismiss} />
```

### SystemMessage (inline banner)
`mode?: 'info' | 'warning' | 'error'`, `header?: string`, `caption?: ReactNode`, `children?` (body), `widthByContent?`.

### SpinLoader / BubblesLoader
`SpinLoader: { color?: string; className?: string }`. `BubblesLoader: { color?; className?; variant?: 'standard' | 'large' }`. BubblesLoader is service-ui's default page/section loader.

### FieldLabel
`extends label HTML props` + `isRequired?: boolean`.

### Icons
~80 named SVGR React components exported from the root (full list in `dist/components/icons/index.d.ts`): `PlusIcon`, `DeleteIcon`, `EditIcon`, `CloseIcon`, `ClearIcon`, `SearchIcon`, `CopyIcon`, `RefreshIcon`, `ExportIcon`, `DownloadIcon`, `ExternalLinkIcon`, `MeatballMenuIcon`, `ChevronDownDropdownIcon`, `ArrowUp/Down/Left/RightIcon`, `CalendarIcon`, `CheckmarkIcon`, `InfoIcon`, `WarningIcon`, `ErrorIcon`, `SuccessIcon`, `FilterFilledIcon`/`FilterOutlineIcon`, `SortIcon`, `FolderIcon`, `PinFilled/OutlineIcon`, `OpenEyeIcon`/`ClosedEyeIcon`, `Priority*Icon`, file-type icons (`CsvIcon`, `PdfIcon`, `XlsIcon`, `JarIcon`, `ImageIcon`), domain icons (`TestCaseIcon`, `TestPlanIcon`, `LaunchTypeIcon`, `RocketIcon`...). Usage: plain `<PlusIcon />` — inline SVG with `currentColor`, sized by context. No generic `<Icon name="...">` component.

**Other components in the kit**: `ActionMenu`/`ActionMenuItem`, `AdaptiveTagList`, `AttachedFile`, `BulkPanel`, `DatePicker`, `FieldTextFlex`, `FileDropArea`, `FiltersButton`/`FilterItem`, `IssueList`, `MaxValueDisplay`, `Selection`, `SortableItem/SortableList/DragLayer/TreeSortableItem/TreeSortableContainer` (require a react-dnd `DndProvider` in the host app), hooks `useTreeDropValidation`, `useEllipsisTitle`.

---

## 3. What's NOT in the kit (and how service-ui fills the gap)

| Gap | In kit? | service-ui's solution |
|---|---|---|
| **Tabs** | No | Custom: `src/components/main/tabs/` (tabs.jsx + tabs.scss), plus `containerWithTabs/` and `navigationTabs/` — plain CSS-module components |
| **Cards / panels** | No | Plain divs + SCSS modules per page |
| **Badges / status pills** | No (Chip is closest) | Custom small components with SCSS |
| **Code/log viewer** | No | Custom log grid; syntax highlighting via `react-syntax-highlighter@15` |
| **Markdown** | No | Custom `components/main/markdown/` |
| **JSON viewer** | No | None — text/pre |
| **Charts** | No | Legacy `c3`, `chart.js`, `d3` under `components/widgets/` — all custom |
| **Layout primitives (Stack/Grid/Box)** | No | Plain SCSS modules |
| **Data-grid with virtualization** | No | ui-kit Table is non-virtualized; service-ui uses `react-virtualized` for big lists |
| **Toast manager** | No (SystemAlert is a single alert) | Custom NotificationContainer + redux |
| **Form state** | No | redux-form wrappers feed `value/onChange/error/touched` into ui-kit fields |

---

## 4. Theming tokens (CSS custom properties in `dist/style.css`)

152 `--rp-ui-*` variables. Reuse them in custom components. Grouped:

**Base palette / brand** — `--rp-ui-base-topaz` (primary brand), `--rp-ui-base-topaz-100/-hover/-pressed/-focused`, `--rp-ui-base-error/-hover/-pressed/-focused`, `--rp-ui-base-light`, `--rp-ui-base-charcoal`, `--rp-ui-base-almost-black(-slight-light)`, `--rp-ui-base-purple-600`, `--rp-ui-base-yellow-500/800`, `--rp-ui-base-a-red-200/400/700`, `--rp-ui-base-a-yellow-300/600/700`, `--rp-ui-base-a-green-800`, `--rp-ui-base-a-raspberry-300`, `--rp-ui-base-bg-000/100/200`, `--rp-ui-base-border-100`, `--rp-ui-base-e-100/200/300/400/91` (elevation greys), dark-mode counterparts `--rp-ui-base-dark-*`.

**Semantic (theme-switched)** — `--rp-ui-color-primary(-hover/-pressed/-focused/-text)`, `--rp-ui-color-error(-hover/-pressed/-focused)`, `--rp-ui-color-text/-2/-3/-secondary`, `--rp-ui-color-bg/-2/-3`, `--rp-ui-color-disabled`, field tokens `--rp-ui-color-field-bg/-2/-3(-hover)`, `--rp-ui-color-field-border(-2/-3, -hover, -disabled)`, `--rp-ui-color-field-hover/-2`, `--rp-ui-color-field-icon`, `--rp-ui-color-field-opened`, `--rp-ui-color-field-placeholder(-3)`, `--rp-ui-color-radio-checked`.

**Typography** — `--rp-ui-base-font-family`, `--rp-ui-base-font-family-heading`, `--rp-ui-base-font-size-md`, `--rp-ui-base-lh-md`, `--rp-ui-base-fw-thin/extra-light/light/regular/medium/semi-bold/bold`.

**Effects/misc** — `--rp-ui-base-shadow(-hover/-secondary)`, `--rp-ui-box-shadow`, `--rp-ui-shadow-rgb`, `--rp-ui-image-shadow`, `--rp-ui-base-overlay(-light-cyan)`, `--rp-ui-base-tooltip-bg`, `--rp-ui-divider-bg`, `--rp-ui-dropdown-selected(-hover)-bg`, `--rp-ui-menu-text-color`, `--rp-ui-opacity-default`, `--rp-ui-base-max-page-width`, `--rp-ui-validation-error-border`.

**Domain tokens** — `--rp-ui-base-test-execution-status-passed/skipped`, `--rp-ui-color-execution-status-btn-passed/failed`, defect groups `--rp-ui-base-product-bug-group`, `--rp-ui-base-automation-bug-group`, `--rp-ui-base-system-issue-group`, `--rp-ui-base-no-defect-bug-group`, `--rp-ui-base-defect-type-AB`, priority icon colors, tag colors `--rp-ui-tag-key-bg/-text`, `--rp-ui-tag-value-bg/-text`, system-message lines `--rp-ui-base-sm-error(-line-100/200)`, `--rp-ui-base-sm-warning(-line-100/200)`, `--rp-ui-base-sm-info-line-100`.

Semantic `--rp-ui-color-*` values flip with the ThemeProvider class; prefer them over `--rp-ui-base-*` in custom components.

---

## 5. Pitfalls (alpha-flaky things)

1. **ESM-only** (`"type": "module"`, no CJS). Vitest/jsdom needs ESM support (fine with Vitest defaults).
2. **One global CSS bundle** — `import '@reportportal/ui-kit/style.css'` once, before your own styles so app CSS can override.
3. **Inconsistent onChange contracts**: Checkbox/Toggle/Radio/FieldText give a DOM `ChangeEvent`; FieldNumber/Dropdown/SegmentedControl give the raw value. Checkbox/Toggle use `value` for the checked boolean, not `checked`.
4. **`error` shows only with `touched`** on FieldText/Dropdown. `FieldText` needs `defaultWidth={false}` for full width.
5. **SegmentedControl is not value-controlled** — selection is `selected` flags on options; rebuild options on change.
6. **Portal props required inside overflow containers**: `Dropdown menuPortalRoot`, `Tooltip portalRoot`, `Popover shouldUsePortal` — otherwise menus clip inside `Modal`/`SidePanel`/scrollable tables.
7. **Autocomplete types over-require**: pass explicit defaults for props declared required.
8. **Modal visibility = mount** (SidePanel does have `isOpen`).
9. **Sortable\*** components require a react-dnd `DndProvider` in the host app (inspector does not need them).
10. **Table `sortableColumns` is typed `string[]`**; sorting is fully controlled (re-sort data yourself or use exported `sortTableData`).
11. **Version coupling**: pin the exact version `0.0.1-alpha.247`, no caret — alpha releases make breaking changes between patch numbers.
12. **Radio `RadioOption.disabled` is required** in the type — set `disabled: false` explicitly per option.
13. **No toast manager, no z-index orchestration** — stacking is the host's problem.
14. **ThemeProvider is class-based**: custom themes are CSS class names authored against the `--rp-ui-*` variables, not JS token objects.
