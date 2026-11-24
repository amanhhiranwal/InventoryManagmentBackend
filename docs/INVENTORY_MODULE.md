# Inventory Module — implementation notes

Living record of the multi-company, multi-warehouse inventory module: what
is reused, what is built, what is left, and how to deploy it. Specs:
`Enterprise_Inventory_Module_Blueprint.pdf` (primary),
`Multi_Company_Warehouse_Inventory_Flow_and.pdf`, `GRN SHEET (1).xlsx`
(45 sheets, GRN 2026-27/001 – /088) and the master prompt.

## 1. UI reuse inventory (frontend)

Every inventory screen is built from components the app already uses:

| Need | Reused from |
|---|---|
| Page shell, title row, KPI grid, toolbar, table card | `components/crm/ListPageShell` (`ListPage`, `ListPageHeader`, `StatGrid`, `ListToolbar`, `PrimaryAction`, `TableCard`, `LIST_TABLE`) — as on Stock Out, Leads, Sales Orders |
| KPI cards | `components/crm/StatCard` |
| Status tabs on tables | `components/crm/QueueTabs` |
| Pagination | `components/crm/Pagination` |
| Modals, buttons, inputs | `components/ui/Modal`, `Button`, `Input`, `Table` |
| Multi-pickers | `components/ui/SearchableMultiSelect` (Users → Warehouse Access) |
| Toasts | `lib/store/ui.store` `addToast` |
| Dates | `lib/datetime` (`formatDate`, `formatDateTime`, `formatDateNumeric`, `toDateInput`) |
| Permission gating (UI only) | `features/auth/utils/permissions.hasPermission`; the server checks every call |
| Navigation | Sidebar is menu-driven (`menu_service.DEFAULT_MENUS_DATA`); new pages sit under **SCM** |
| Roles & Access | `app/rbac/page.tsx` builds ticks from the menu plus `PAGE_ACTIONS` |
| Stock-In page layout | Figma "Stock-In" frame: left card Purchase Order Details + Products & Order Items, right card Stock-In Process; statuses from "Status for Stock in" |

## 2. Architecture decisions

* **Stock in PostgreSQL**: ledger, balances, serials, documents, GRN. The
  office MongoDB is standalone (no multi-document transactions), so it
  cannot post atomically.
* **Products stay in MongoDB for now** (`inventory_items`), as instructed
  on 23 Nov. The ledger keys products by **SKU = catalogue model number**.
  The master prompt asks for product/variant tables in PostgreSQL —
  **open decision**, see §6.
* **Warehouses reuse `locations`** with `location_type` WAREHOUSE/OFFICE.
* **One posting service** (`app/services/stock/posting.py`) is the only
  writer of `inventory_ledger` and `stock_balance`.
* **Signed quantities**: ledger `quantity` is positive in, negative out.
  `stock_balance.qty_on_hand` is per (company, warehouse, bin, SKU,
  stock_status). `Available = UNRESTRICTED on hand − reserved`.
  QUARANTINE, REJECTED, BLOCKED and IN_TRANSIT are separate status rows and
  are never available.
* **Numbering**: GRN `2026-27/001` per company and financial year (the
  workbook's own series); opening stock `{COMPANY_CODE}-OPN-2026-00001`.
  Both via the forward-only `document_counter`.
* **Schema changes** ship the way this repo ships them: models +
  `sync_schema.py` (adds tables/columns) + `apply_release_setup.py` (data
  steps) + `check_and_seed_db.py` (permissions). New tables only; nothing
  dropped.
* **Permission names** follow the repo's existing style (`grn.read`,
  `stock.read`, like `stock_out.read`), not `inventory.grn.view`.

## 3. Gap checklist

| Area | Status | Where |
|---|---|---|
| Company master, user ↔ company | EXISTS | `companies`, `user_companies`, `CompanyScopeService` |
| Warehouse master | EXTEND (done) | `locations.location_type` |
| Bins STORE / QC / REJECT | NEW (done) | `warehouse_bins`, auto-created per warehouse |
| User ↔ warehouse access | NEW (done) | `user_warehouses`, Users → Warehouse Access |
| Warehouse scope on server | EXTEND (done) | `CompanyScopeService.visible_warehouse_ids / assert_warehouse_allowed` |
| Ledger, balances, serials | NEW (done) | `inventory_ledger`, `stock_balance`, `stock_serial` |
| Posting service, idempotency, locks | NEW (done) | `services/stock/posting.py` |
| Reconciliation | NEW (done) | `GET /stock/reconcile`, `reconcile_stock.py` (needs a cron) |
| Opening stock (maker/checker) | NEW (done) | `stock_document`, SCM → Warehouse → Opening Stock |
| Mongo stock → ledger migration | NEW (done, not run) | `migrate_stock_to_ledger.py` (dry run / `--apply`) |
| PR, approval chain, vendors, offline quotations, PO | EXISTS | procurement module, unchanged |
| GRN with quarantine-first QC | NEW (done) | `goods_receipt(_line)`, SCM → Stock In |
| PO received qty / status from GRN | EXTEND (done) | at receipt posting |
| Roles & Access ticks for every enforced action | EXTEND (done) | `PAGE_ACTIONS` |
| Product / variant / UOM tables in PostgreSQL | OPEN DECISION | §6 |
| Company product settings (reorder, QC flag, tolerance) | NEW (to do) | |
| Vendor return | NEW (done) | against a posted GRN, Warehouse → Vendor Returns |
| Sales order lines, reservations, dispatch orders, pick/pack, POD | NEW (done) | SCM → Dispatch |
| Same-company transfers via in-transit | NEW (done) | SCM → Transfers; inter-company refused |
| Customer returns linked to dispatch, adjustments, cycle counts | NEW (done) | RETURN GRN + QC; Warehouse → Adjustments; SCM → Stock Count |
| Dashboards, reports, CSV, alerts | NEW (partly: Warehouse page) | Phase 7 |

## 4. Status by phase

| Phase | State | Tests (live, office DB) |
|---|---|---|
| 1 Analysis | Done (blueprint PDF) | — |
| 2 Companies, warehouses, access | Built, not committed | `test_warehouse_access.py` 42/0 |
| 3 Ledger, balances, opening stock | Built, not committed | `test_stock_ledger.py` 46/0 |
| 4 Stock In (GRN + QC) | Built, not committed | `test_grn.py` 57/0 |
| 5 Stock Out | Built, not committed (screens, `stock_ledger_live` switch) | `test_dispatch.py` 46/0, `test_ledger_live.py` 10/0 |
| 6 Transfers, returns, adjustments, counts | Built, not committed | `test_adjustments_returns.py` 21/0, `test_transfers_counts.py` 44/0 |
| 7 Dashboards, reports, alerts | Warehouse page only | |
| 8 Hardening | Not started | |

## 5. GRN flow (as built)

`DRAFT` (storekeeper, `grn.create`) → `SUBMITTED` (sent in; no stock) →
**post receipt** (`grn.post`): all received qty → `QUARANTINE` in the QC
bin, `PURCHASE_RECEIPT` (or `CUSTOMER_RETURN`), PO received qty + status
→ `QC_PENDING` → **quality check** (`grn.approve`): `QC_STATUS_TRANSFER`
out of quarantine; accepted → `UNRESTRICTED` (store bin, serials),
rejected → `REJECTED`, damaged → `BLOCKED` (REJECT bin) → `POSTED`
("GRN Generated"). `CANCELLED` only before the receipt is posted.
Over-receipt is refused at save and again under a PO row lock at receipt
posting. Receipt types: PURCHASE (PO required), RETURN, OTHER (supplier
named). Print layout follows the workbook.

## 5a. Phase 6 flows (as built)

**Transfers** (`stock_transfer(_line)`, `/stock/transfers`): `DRAFT` →
`SUBMITTED` → `APPROVED` (not by the requester, `transfer.approve`) →
**ship** (`transfer.ship`, vehicle or docket required): `TRANSFER_OUT` takes
the goods out of the sending store bin and puts the same quantity
`IN_TRANSIT` in the receiving warehouse's store bin; reserved stock cannot
ship; serials travel (`IN_TRANSIT`) → **receive** (`transfer.receive`, a
user of the receiving warehouse): in transit falls by everything shipped;
good → `UNRESTRICTED` (bin chosen), damaged → `BLOCKED` (REJECT bin),
shortfall written off as `STOCK_ADJUSTMENT_OUT` (serials `LOST`); shortage
or damage needs a remark → `RECEIVED`. Cancel only before shipping. A
warehouse of another company is refused: that is a sale and a purchase.
The receiver records the shortfall write-off without a second approval;
the remark and the ledger row are the record.

**Customer returns**: a `RETURN` GRN may name the dispatch the goods went
out on (`dispatch_order_id`, each line a `dispatch_line_id`): only what
went out on that line, less other returns, can come back — checked at save
and again under a dispatch row lock at receipt posting. Same company only.
Goods go to `QUARANTINE`; QC decides: accepted = restock, rejected = for
return to vendor or scrap, damaged = blocked for repair. A return without
a dispatch on record is still allowed (dealer returns, pre-ledger sales).

**Cycle counts** (`stock_count(_line)`, `/stock/counts`): starting snapshots
`UNRESTRICTED` store-bin balances; counting is blind on screen; products
found can be added; `SUBMITTED` → approve (not the counter,
`stock.count.approve`; above the finance limit also `stock.adjust.finance`)
posts each difference as `STOCK_ADJUSTMENT_IN/OUT`. Refused if a counted
balance moved since the snapshot (rows locked for the check), and for
differences on serial-numbered products (use an adjustment naming serials).

## 6. Open decisions

1. **Product master location.** Products in MongoDB (current, as you
   instructed) or move product + variant + UOM to PostgreSQL (master
   prompt / blueprint). Moving means a catalogue migration and repointing
   the Inventory page, pickers and sales lines. The ledger is keyed by SKU
   either way, so built work is not lost.
2. **Behaviour changes in Phase 5**: stock leaves at dispatch commit (not
   at READY), and cancelling after dispatch no longer restores stock.
3. **Shared products** (no company) need a company before migration.
4. **GRN starting number** on go-live (the workbook is at 2026-27/088).
5. **Over-receipt tolerance / QC-not-required products**: need the
   company product settings table; today every receipt goes through QC
   and over-receipt is always refused.

## 7. Deploy and migration

1. Back up PostgreSQL.
2. Deploy: `sync_schema.py` creates the new tables;
   `check_and_seed_db.py` seeds `stock.*` and `grn.*` permissions;
   `apply_release_setup.py` types locations and creates bins.
3. Set a default warehouse per company (Locations) and give people
   Warehouse Access (Users) and the new ticks (Roles & Access).
4. `python migrate_stock_to_ledger.py` (dry run), review, then `--apply`.
5. Schedule `python reconcile_stock.py` nightly (exits 1 on mismatch).

Rollback: the new tables are additive; dropping `goods_receipt*`,
`stock_*`, `inventory_ledger`, `warehouse_bins`, `user_warehouses` and the
`locations.location_type` column returns the schema to before. MongoDB
`inventory_items` and `inventory_movements` are not modified by any of
this.
