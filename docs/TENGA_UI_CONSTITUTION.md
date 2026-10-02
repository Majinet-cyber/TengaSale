# Tenga UI constitution

The single source of truth (SST) is `static/css/tenga-design-system.css`. This constitution applies to public, authentication, merchant, underwriting, HQ, merchant-admin, support and customer surfaces. Layouts can differ; brand assets and interaction primitives cannot.

## Brand assets

Use `partials/tenga_brand.html`, never hard-code a new logo path in a template. The default compact mark is the approved `static/images/Tenga.png`, the identity already established by the latest brand commits and identity tests. Inspection of `static/images/brand/` also identified the approved full lockup `tengasale-logo-full.png`; the include exposes it with `variant="full"`. The full lockup embeds legacy artwork/text, so current headers deliberately use the compact mark. The other historical variants remain assets, not alternative page-by-page choices. Do not recreate the mark with text, CSS, emoji, inline SVG or image-error fallbacks. `partials/brand_logo.html` is a compatibility wrapper around the same include. Never invent a motto.

## Colors and type

The approved existing product token was `#f4511e`; it is now named `--tenga-brand-orange`. `--tenga-brand`, `--ts-orange` and signal/type brand names alias that token. Hover is `--tenga-brand-orange-hover`. Do not use historical alternatives such as #ff5a00 or #ff6b2c for new brand styling.

Use semantic `--tenga-ink`, `--tenga-text-muted`, `--tenga-canvas`, `--tenga-surface`, `--tenga-border`, `--tenga-success`, `--tenga-warning` and `--tenga-danger`, including their soft state surfaces. Orange means brand/action, blue means workflow, state colors mean actual state.

HQ owns a composition theme, not a second design system. Its navy frame, warm interior, blue/purple gradient and selective cyan use `--tenga-pulse-navy`, `--tenga-pulse-surface`, `--tenga-pulse-canvas`, `--tenga-pulse-blue`, `--tenga-pulse-purple`, `--tenga-pulse-cyan`, `--tenga-pulse-gradient`. There is no separate HQ orange.

The only app font is `--tenga-font-family`: Inter with system fallbacks. `tenga-type-system.css` owns semantic roles: display, page title/intro, section title/label, card/row title, body/supporting body, metadata/caption, metric, money and status. Existing `.t-type-*` classes expose those roles. Use tabular lining numerals for money and counts. Do not load another font for a particular page.

## Geometry and controls

Spacing tokens follow 4, 8, 12, 16, 20, 24, 32, 40 and 48px: `--tenga-space-1/2/3/4/5/6/8/10/12`. Control radius is `--tenga-radius-control` (10px), card radius `--tenga-radius-card` (22px), hero/frame radius `--tenga-radius-hero` (26px). Compatibility small/medium/large tokens exist for current screens. Use `--tenga-shadow-surface`, `--tenga-shadow-hover`, `--tenga-shadow-hero` and `--tenga-shadow-frame`; do not invent per-template shadow recipes.

Use existing field/button/card primitives, semantic heading levels and real links/buttons. Controls need visible keyboard focus; icon actions are 44 × 44px. Tables scroll within their own wrapper. Respect reduced motion. Avoid decorative card borders and avoid broad global overrides for specialist layout needs.

## Shared header, icons and country

`partials/tenga_topbar.html` accepts `title`, a named `home_url`, optional `header_class`, `title_class` and `title_testid`. Underwriter preserves its inherited page-heading block and composes the same identity and action primitives.

`partials/tenga_topbar_actions.html` fixes the order: country, WhatsApp support, notifications with real unread count, CSRF-protected POST logout. Bootstrap Icons is the canonical UI icon language. WhatsApp lives in `partials/tenga_whatsapp.html`; if support is unconfigured, the existing contact route is an honest fallback. Do not fabricate a support number.

`includes/flag_pill.html` is the only compact country component. It reuses the approved Malawi flag partial, shows no visible country name, and exposes `aria-label="Malawi"`. Marketing copy such as “Live in Malawi. Built for Southern Africa.” is unaffected. Existing geographic map artwork and product illustrations are illustrations, not an alternative control-icon system.

## Cascade and ownership

1. Legacy foundation: style.css, tengasale.css, premium.css. Retained for untouched application modules.
2. Existing component utilities: hq-shell.css (legacy shared card/modal/loading utilities), product/card/signal styles. Their token definitions now alias the SST.
3. Semantic typography: tenga-type-system.css.
4. Canonical token/control layer: tenga-design-system.css. Loaded after legacy layers during migration so its definitions prevail.
5. Role composition: hq-sales-pulse.css, loaded only for HQ and responsible for the complete frame/navigation/home. The command page no longer loads competing hq-command-home or hq-command-v2 styles. Public composition stays in tenga-landing-v4.css and auth composition in tenga-auth.css.

Do not add another global stylesheet to fix one page. Do not reintroduce huge template-local style blocks or endless !important rules. The remaining legacy global selectors are migration debt, not examples for new work. Change the canonical definition and aliases when changing a primitive.

## HQ navigation and functional preservation

The shared segmented navigation exposes frequent destinations. Native `details` opens a grouped All tools directory, available on every HQ page and linked from mobile More. Staff/developer/admin gates remain intact. Detail links, exports and POST actions stay on their existing destination pages; route names, business logic, permissions and models do not change.

`TENGA_HQ_ROUTE_AUDIT.json` records the 4a8cacc baseline: 83 distinct hq_* names (including aliases/detail/action/export routes) and 47 HQ-template destinations (including non-HQ support/account destinations). Compare both sets before shipping; do not confuse a route inventory with 83 independent menu items.

## Public catalogue and truthfulness

Live active, eligible DeviceDeal records take priority. Only when no eligible public offer is available does the server expose the three historical illustrative plans from d33c5c6. These are labelled as illustrative; pricing and availability are confirmed during application. Catalogue data belongs on the server, interaction in JavaScript. No demo records are written to the database. Historical assets did not provide verified A50C/Spark 50/A15 photographs: use the labelled generic existing device illustration, never a Camon or another Samsung model disguised as the offered device. Solar PayGo remains coming soon, with the existing support flow used for enquiries.

## Verification before release

Run Django checks, relevant website/dashboard/auth/application tests, collectstatic, route/action comparisons and responsive browser checks at 360, 390, 768, 1024, 1366 and 1440px. Test empty and populated live catalogues, cadence and phone selection, support validation, map switching/Auto, simulation, keyboard controls and CSRF logout. Never claim a check passed unless it ran. No schema migration belongs in this UI refactor.
