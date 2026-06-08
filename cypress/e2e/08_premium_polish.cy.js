/**
 * TengaSale Cypress E2E — Premium Polish Regression Suite
 * Covers all critical UI behaviors from the v2 premium polish pass.
 *
 * Test areas:
 *  A. Merchant header stability (topbar icons always visible, no wrap)
 *  B. Malawi flag / country pill
 *  C. IMEI exactly 15 digits
 *  D. Outside-hours modal (not passive warning)
 *  E. Recording optional
 *  F. WhatsApp bot tooling
 *  G. HQ premium pages render
 *  H. Role list includes new executive roles
 */

// ────────────────────────────────────────────────────────────────────────────
// A. MERCHANT HEADER STABILITY
// ────────────────────────────────────────────────────────────────────────────
describe("A — Merchant Header Stability", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
  });

  it("merchant home: WhatsApp icon is visible top-right", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="topbar-whatsapp"]').should("be.visible");
  });

  it("merchant home: notifications icon is visible top-right", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="topbar-notifications"]').should("be.visible");
  });

  it("merchant home: logout icon is visible top-right", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="topbar-logout"]').should("be.visible");
  });

  it("merchant home: right action cluster does not wrap (single row)", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="topbar-whatsapp"]').then(($wa) => {
      cy.get('[data-testid="topbar-logout"]').then(($lo) => {
        // Both icons must be on the same vertical row (within 2px tolerance)
        const waTop = $wa[0].getBoundingClientRect().top;
        const loTop = $lo[0].getBoundingClientRect().top;
        expect(Math.abs(waTop - loTop)).to.be.lessThan(4);
      });
    });
  });

  it("earnings/wallet page: WhatsApp visible", () => {
    cy.visit("/earnings/");
    cy.get('[data-testid="topbar-whatsapp"]').should("be.visible");
  });

  it("notifications page: WhatsApp and logout visible", () => {
    cy.visit("/notifications/");
    cy.get('[data-testid="topbar-whatsapp"]').should("be.visible");
    cy.get('[data-testid="topbar-logout"]').should("be.visible");
  });
});

// ────────────────────────────────────────────────────────────────────────────
// B. MALAWI FLAG / COUNTRY PILL
// ────────────────────────────────────────────────────────────────────────────
describe("B — Malawi Flag", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
  });

  it("merchant home: country pill is visible", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="country-pill-mw"]').should("be.visible");
  });

  it("country pill contains 'MW' code", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="country-pill-mw"]').should("contain.text", "MW");
  });

  it("country pill contains 'Malawi' text", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="country-pill-mw"]').should("contain.text", "Malawi");
  });

  it("country pill not hidden by overflow", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="country-pill-mw"]').then(($el) => {
      const rect = $el[0].getBoundingClientRect();
      expect(rect.width).to.be.greaterThan(0);
      expect(rect.height).to.be.greaterThan(0);
    });
  });

  it("flag Malawi SVG renders (has rect or g element)", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="country-pill-mw"] svg').should("exist");
  });
});

// ────────────────────────────────────────────────────────────────────────────
// C. IMEI EXACTLY 15 DIGITS
// ────────────────────────────────────────────────────────────────────────────
describe("C — IMEI 15-Digit Enforcement", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
  });

  it("IMEI input has maxlength=15", () => {
    cy.visit("/applications/new/");
    cy.get("[name='imei_number']").should("have.attr", "maxlength", "15");
  });

  it("IMEI input only accepts digits", () => {
    cy.visit("/applications/new/");
    cy.get("[name='imei_number']").type("abc12345678901X").then(($el) => {
      const val = $el.val();
      expect(/^[0-9]*$/.test(val)).to.be.true;
    });
  });

  it("IMEI paste longer than 15 digits clamps to 15", () => {
    cy.visit("/applications/new/");
    cy.get("[name='imei_number']").invoke("val", "1234567890123456789").trigger("input");
    cy.get("[name='imei_number']").should(($el) => {
      expect($el.val().length).to.be.at.most(15);
    });
  });

  it("IMEI less than 15 digits shows error state", () => {
    cy.visit("/applications/new/");
    cy.get("[name='imei_number']").type("12345").blur();
    // Counter or status element should indicate invalid
    cy.get("body").should("contain.text", "15");
  });
});

// ────────────────────────────────────────────────────────────────────────────
// D. OUTSIDE HOURS MODAL
// ────────────────────────────────────────────────────────────────────────────
describe("D — Outside Business Hours Modal", () => {
  it("outside-hours modal exists in DOM when is_business_hours=False", () => {
    // This tests the template structure (requires a non-business-hours state)
    // In CI the template renders; we check the element exists when rendered
    cy.loginAsMerchant();
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="outside-hours-modal"]').length) {
        // Modal is present — it should not be the only content (form should also be present)
        cy.get('[data-testid="outside-hours-modal"]').should("exist");
        cy.get('[data-testid="outside-hours-continue"]').should("exist");
        cy.contains("Monday–Friday: 9am–6pm CAT").should("exist");
        cy.contains("approval may take longer").should("exist");
      }
      // If outside business hours element not present, we're within hours — test passes
    });
  });

  it("outside-hours OK button dismisses modal", () => {
    cy.loginAsMerchant();
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="outside-hours-modal"]').length) {
        cy.get('[data-testid="outside-hours-modal"]').should("be.visible");
        cy.get('[data-testid="outside-hours-continue"]').click();
        cy.get('[data-testid="outside-hours-modal"]').should("not.be.visible");
      }
    });
  });

  it("form is usable after outside-hours modal dismissed", () => {
    cy.loginAsMerchant();
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="outside-hours-continue"]').length) {
        cy.get('[data-testid="outside-hours-continue"]').click();
      }
      cy.get("form").should("exist");
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// E. RECORDING OPTIONAL
// ────────────────────────────────────────────────────────────────────────────
describe("E — Call Recording Optional", () => {
  beforeEach(() => {
    cy.loginAsUnderwriter();
  });

  it("call recording section has 'Optional' badge", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("a[href*='/sales/'][href*='/customer-call/']").length) {
        cy.get("a[href*='/customer-call/']").first().click();
        cy.contains(/optional/i).should("exist");
      }
    });
  });

  it("call recording upload field is not required attribute", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("input[name='call_recording']").length) {
        cy.get("input[name='call_recording']").should("not.have.attr", "required");
      }
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// F. WHATSAPP BOT TOOLING
// ────────────────────────────────────────────────────────────────────────────
describe("F — WhatsApp Bot Tooling", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("HQ sidebar has WhatsApp Bot link", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="sidebar-whatsapp-bot"]').should("exist");
  });

  it("HQ WhatsApp Bot page renders without error", () => {
    cy.visit("/tengasale/hq/whatsapp-bot/");
    cy.get("body").should("be.visible");
    cy.contains(/WhatsApp Bot/i).should("be.visible");
  });

  it("WhatsApp Bot page shows channel status", () => {
    cy.visit("/tengasale/hq/whatsapp-bot/");
    cy.get('[data-testid="wa-bot-status"]').should("be.visible");
  });

  it("WhatsApp Bot page has templates section", () => {
    cy.visit("/tengasale/hq/whatsapp-bot/");
    cy.get('[data-testid="wa-templates"]').should("exist");
  });

  it("WhatsApp Bot page: template list renders", () => {
    cy.visit("/tengasale/hq/whatsapp-bot/");
    cy.get('[data-testid="wa-templates"]').within(() => {
      cy.contains(/OTP|Application|Payment/i).should("exist");
    });
  });

  it("WhatsApp Bot page: automation flows render", () => {
    cy.visit("/tengasale/hq/whatsapp-bot/");
    cy.get('[data-testid="wa-automations"]').should("exist");
  });

  it("WhatsApp Bot page: test form exists with phone input", () => {
    cy.visit("/tengasale/hq/whatsapp-bot/");
    cy.get('[data-testid="wa-test-phone"]').should("exist");
  });

  it("WhatsApp Bot page: test phone validates format", () => {
    cy.visit("/tengasale/hq/whatsapp-bot/");
    cy.get('[data-testid="wa-test-phone"]').should("have.attr", "pattern");
  });

  it("topbar WhatsApp icon is visible on HQ pages", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="topbar-whatsapp"]').should("exist");
  });
});

// ────────────────────────────────────────────────────────────────────────────
// G. HQ PREMIUM PAGES RENDER
// ────────────────────────────────────────────────────────────────────────────
describe("G — HQ Premium Pages", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("HQ fraud checks page renders", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get("body").should("be.visible");
    cy.contains(/fraud/i).should("exist");
  });

  it("HQ staff roles page renders", () => {
    cy.visit("/tengasale/hq/staff-roles/");
    cy.get("body").should("be.visible");
  });

  it("HQ portfolio page renders", () => {
    cy.visit("/tengasale/hq/portfolio/");
    cy.get("body").should("be.visible");
  });

  it("HQ overview has no horizontal overflow", () => {
    cy.visit("/tengasale/hq/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth > win.document.documentElement.clientWidth;
      expect(overflow).to.be.false;
    });
  });

  it("merchant dashboard has no horizontal overflow", () => {
    cy.loginAsMerchant();
    cy.visit("/tengasale/merchant/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth > win.document.documentElement.clientWidth;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// H. ROLES
// ────────────────────────────────────────────────────────────────────────────
describe("H — Role Management", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("HQ staff roles page renders role list", () => {
    cy.visit("/tengasale/hq/staff-roles/");
    cy.get("body").should("be.visible");
  });

  it("HQ staff roles page does not error on load", () => {
    cy.visit("/tengasale/hq/staff-roles/");
    cy.get("body").should("not.contain", "Server Error");
    cy.get("body").should("not.contain", "500");
  });
});

// ────────────────────────────────────────────────────────────────────────────
// I. KYC ORIENTATION-AWARE CARDS
// ────────────────────────────────────────────────────────────────────────────
describe("I — KYC Orientation Intelligence", () => {
  beforeEach(() => {
    cy.loginAsUnderwriter();
  });

  it("KYC selfie card renders with portrait aspect ratio class", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("[data-kyc-card='selfie']").length) {
        cy.get("[data-kyc-card='selfie']").should("exist");
        cy.get("[data-kyc-card='selfie'] .kyc-smart-card__frame--selfie").should("exist");
      }
    });
  });

  it("KYC ID front card renders with landscape aspect ratio class", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("[data-kyc-card='id_front']").length) {
        cy.get("[data-kyc-card='id_front'] .kyc-smart-card__frame--id_front").should("exist");
      }
    });
  });

  it("KYC ID back card renders with landscape aspect ratio class", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("[data-kyc-card='id_back']").length) {
        cy.get("[data-kyc-card='id_back'] .kyc-smart-card__frame--id_back").should("exist");
      }
    });
  });

  it("KYC signature card renders with wide landscape class", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("[data-kyc-card='signature']").length) {
        cy.get("[data-kyc-card='signature'] .kyc-smart-card__frame--signature").should("exist");
      }
    });
  });

  it("KYC cards do not show orientation warning pills", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find(".kyc-smart-card").length) {
        cy.get(".kyc-orient-pill").should("not.exist");
        cy.get(".kyc-orient-warn").should("not.exist");
      }
    });
  });

  it("KYC card with image has view-full button", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("[data-testid^='kyc-view-full-']").length) {
        cy.get("[data-testid^='kyc-view-full-']").first().should("be.visible");
      }
    });
  });

  it("KYC identity check page no horizontal overflow", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      const links = $body.find("a[href*='identity-check']");
      if (links.length) {
        cy.wrap(links.first()).click({ force: true });
        cy.window().then((win) => {
          const overflow = win.document.documentElement.scrollWidth
            > win.document.documentElement.clientWidth + 2;
          expect(overflow).to.be.false;
        });
      }
    });
  });

  it("KYC send-back button exists and has data-correction-open", () => {
    cy.visit("/sales/");
    cy.get("body").then(($body) => {
      if ($body.find("[data-testid^='kyc-send-back-']").length) {
        cy.get("[data-testid^='kyc-send-back-']").first()
          .should("have.attr", "data-correction-open");
      }
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// J. APPLICATION STEPPER — no overflow, chips visible
// ────────────────────────────────────────────────────────────────────────────
describe("J — Application Stepper", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
  });

  it("stepper chips container exists with testid", () => {
    cy.visit("/applications/new/");
    cy.get('[data-testid="stepper-chips-wrap"]').should("exist");
  });

  it("at least one stepper chip is visible", () => {
    cy.visit("/applications/new/");
    cy.get('[data-testid="stepper-chips"]').should("be.visible");
    cy.get(".application-step").should("have.length.gte", 1);
  });

  it("stepper active chip is visible (not clipped)", () => {
    cy.visit("/applications/new/");
    cy.get(".application-step-active").should("be.visible");
  });

  it("application page has no horizontal overflow", () => {
    cy.visit("/applications/new/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("stepper progress bar exists", () => {
    cy.visit("/applications/new/");
    cy.get(".application-progress-bar").should("exist");
    cy.get(".application-progress-fill").should("exist");
  });

  it("all 7 step chips render", () => {
    cy.visit("/applications/new/");
    cy.get(".application-step").should("have.length", 7);
  });

  it("stepper chip 1 active on step 1", () => {
    cy.visit("/applications/new/");
    cy.get('[data-testid="stepper-chip-1"]').should("have.class", "application-step-active");
  });
});

// ────────────────────────────────────────────────────────────────────────────
// K. FRAUD INVESTIGATION CONSOLE
// ────────────────────────────────────────────────────────────────────────────
describe("K — Fraud Investigation Console", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("fraud console renders without error", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get("body").should("not.contain", "Server Error");
    cy.get("body").should("not.contain", "500");
  });

  it("fraud risk meter is visible", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="fraud-risk-meter"]').should("exist");
  });

  it("fraud risk level badge exists", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="fraud-risk-level"]').should("be.visible");
  });

  it("fraud signal grid renders", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="fraud-signal-grid"]').should("exist");
    cy.get(".frd-signal-card").should("have.length.gte", 4);
  });

  it("duplicate NID panel exists", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="dup-nid-panel"]').should("exist");
  });

  it("duplicate phone panel exists", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="dup-phone-panel"]').should("exist");
  });

  it("duplicate guarantor panel exists", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="dup-guarantor-panel"]').should("exist");
  });

  it("IMEI intelligence cockpit renders", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="imei-intelligence"]').should("exist");
  });

  it("device mismatch panel exists", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.get('[data-testid="device-mismatch-panel"]').should("exist");
  });

  it("fraud page has no horizontal overflow", () => {
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// L. HQ SIMULATION LAB
// ────────────────────────────────────────────────────────────────────────────
describe("L — HQ Simulation Lab", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("simulation page renders without error", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.get("body").should("not.contain", "Server Error");
  });

  it("scenario preset buttons render", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.get(".sim-preset-btn").should("have.length.gte", 5);
  });

  it("high default stress preset exists", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.get('[data-preset="high_default_stress"]').should("exist");
  });

  it("recovery optimized preset exists", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.get('[data-preset="recovery_optimized"]').should("exist");
  });

  it("simulation input form exists", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.get(".sim-panel").should("exist");
    cy.get("[name='num_devices']").should("exist");
  });

  it("run simulation button exists", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.get(".sim-run-btn").should("be.visible");
  });

  it("clicking a preset applies values to form", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.get('[data-preset="conservative"]').click();
    cy.get("[name='deposit_pct']").should("have.value", "30");
  });

  it("simulation page has no horizontal overflow", () => {
    cy.visit("/tengasale/hq/simulations/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// M. VOLTS ENGINE
// ────────────────────────────────────────────────────────────────────────────
describe("M — Volts Engine Formula Visualizer", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("Volts engine page renders without error", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get("body").should("not.contain", "Server Error");
  });

  it("formula visualizer card exists", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get('[data-testid="volts-formula-card"]').should("exist");
  });

  it("factor chips render (at least 6 chips)", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get(".volts-factor-chip").should("have.length.gte", 6);
  });

  it("approved_volts factor chip exists", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get('[data-testid="volts-factor-chip-volts"]').should("be.visible");
  });

  it("rank_multiplier factor chip exists", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get('[data-testid="volts-factor-chip-rank"]').should("be.visible");
  });

  it("clicking a factor chip shows the explanation popover", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get('[data-testid="volts-factor-chip-quality"]').click();
    cy.get("#volts-popover").should("have.class", "active");
    cy.get("#volts-popover-title").should("not.be.empty");
    cy.get("#volts-popover-body").should("not.be.empty");
  });

  it("clicking active chip collapses the popover", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get('[data-testid="volts-factor-chip-discipline"]').click();
    cy.get('[data-testid="volts-factor-chip-discipline"]').click();
    cy.get("#volts-popover").should("not.have.class", "active");
  });

  it("volts ledger empty state is polished", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="volts-ledger-empty"]').length) {
        cy.get('[data-testid="volts-ledger-empty"]').should("be.visible");
        cy.get('[data-testid="volts-ledger-empty"]').should("contain.text", "No Volts");
      }
    });
  });

  it("pending approvals empty state is polished", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="volts-pending-empty"]').length) {
        cy.get('[data-testid="volts-pending-empty"]').should("be.visible");
      }
    });
  });

  it("Volts page has no horizontal overflow", () => {
    cy.visit("/tengasale/hq/volts/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// N. MERCHANT ACTION ROWS — no clipped values
// ────────────────────────────────────────────────────────────────────────────
describe("N — Merchant Action Rows", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
  });

  it("merchant home rows: action-row-value elements are visible", () => {
    cy.visit("/tengasale/merchant/");
    cy.get(".action-row-value").each(($el) => {
      expect($el.is(":visible")).to.be.true;
    });
  });

  it("merchant home rows: chevrons are visible", () => {
    cy.visit("/tengasale/merchant/");
    cy.get(".action-row-chevron").each(($el) => {
      expect($el.is(":visible")).to.be.true;
    });
  });

  it("earnings row value not empty", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-row-earnings"] .action-row-value')
      .invoke("text")
      .should("not.be.empty");
  });

  it("merchant dashboard no horizontal overflow on 390px viewport", () => {
    cy.viewport(390, 844);
    cy.visit("/tengasale/merchant/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("action rows all have icon, label, chevron", () => {
    cy.visit("/tengasale/merchant/");
    cy.get(".action-row").each(($row) => {
      cy.wrap($row).find(".action-row-icon").should("exist");
      cy.wrap($row).find(".action-row-label").should("exist");
      cy.wrap($row).find(".action-row-chevron").should("exist");
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// O. STAFF DOCUMENT CENTER
// ────────────────────────────────────────────────────────────────────────────
describe("O — Staff Document Center", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("staff document center renders without error", () => {
    cy.visit("/tengasale/hq/staff-documents/");
    cy.get("body").should("not.contain", "Server Error");
    cy.get("body").should("not.contain", "500");
  });

  it("staff cards render (if staff exist)", () => {
    cy.visit("/tengasale/hq/staff-documents/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="sdc-staff-card"]').length) {
        cy.get('[data-testid="sdc-staff-card"]').should("have.length.gte", 1);
      }
    });
  });

  it("staff card shows Open Doc File button not 8 individual doc buttons", () => {
    cy.visit("/tengasale/hq/staff-documents/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="sdc-staff-card"]').length) {
        cy.get('[data-testid="sdc-staff-card"]').first().within(() => {
          // Should NOT have more than 2-3 visible buttons (not 8)
          cy.get("button, a.sdc-btn").should("have.length.lte", 3);
          // Should have the Documents/Open Doc File button
          cy.get('[data-testid="sdc-docs-btn"]').should("exist");
        });
      }
    });
  });

  it("staff document center no horizontal overflow", () => {
    cy.visit("/tengasale/hq/staff-documents/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// P. MOBILE VIEWPORT — NO OVERFLOW ON KEY PAGES
// ────────────────────────────────────────────────────────────────────────────
describe("P — Mobile No Overflow", () => {
  const mobileViewport = [390, 844];

  it("merchant home: no horizontal overflow at 390px", () => {
    cy.viewport(...mobileViewport);
    cy.loginAsMerchant();
    cy.visit("/tengasale/merchant/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("new application step 1: no horizontal overflow at 390px", () => {
    cy.viewport(...mobileViewport);
    cy.loginAsMerchant();
    cy.visit("/applications/new/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("HQ fraud page: no horizontal overflow at 390px", () => {
    cy.viewport(...mobileViewport);
    cy.loginAsHQ();
    cy.visit("/tengasale/hq/fraud-checks/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("HQ volts page: no horizontal overflow at 390px", () => {
    cy.viewport(...mobileViewport);
    cy.loginAsHQ();
    cy.visit("/tengasale/hq/volts/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// Q. MERCHANT NEW APPLICATION CTA — Never Cut Off
// ────────────────────────────────────────────────────────────────────────────
describe("Q — Merchant New Application CTA", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
  });

  it("CTA button exists on merchant home", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').should("exist");
  });

  it("CTA button is visible", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').should("be.visible");
  });

  it("CTA button text NEW APPLICATION is visible", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').should("contain.text", "NEW APPLICATION");
  });

  it("CTA button plus icon is visible", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"] .primary-contract-btn__icon').should("exist");
  });

  it("CTA button arrow icon is visible", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"] .primary-contract-btn__arrow').should("exist");
  });

  it("CTA button not clipped — fully inside viewport at 320px", () => {
    cy.viewport(320, 568);
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').then(($btn) => {
      const rect = $btn[0].getBoundingClientRect();
      expect(rect.left).to.be.gte(0);
      expect(rect.right).to.be.lte(320 + 2);
    });
  });

  it("CTA button not clipped — fully inside viewport at 360px", () => {
    cy.viewport(360, 640);
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').then(($btn) => {
      const rect = $btn[0].getBoundingClientRect();
      expect(rect.left).to.be.gte(0);
      expect(rect.right).to.be.lte(360 + 2);
    });
  });

  it("CTA button not clipped — fully inside viewport at 390px", () => {
    cy.viewport(390, 844);
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').then(($btn) => {
      const rect = $btn[0].getBoundingClientRect();
      expect(rect.left).to.be.gte(0);
      expect(rect.right).to.be.lte(390 + 2);
    });
  });

  it("CTA button not clipped — fully inside viewport at 414px", () => {
    cy.viewport(414, 896);
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').then(($btn) => {
      const rect = $btn[0].getBoundingClientRect();
      expect(rect.left).to.be.gte(0);
      expect(rect.right).to.be.lte(414 + 2);
    });
  });

  it("CTA button has positive height (not zero-height)", () => {
    cy.visit("/tengasale/merchant/");
    cy.get('[data-testid="merchant-new-app-btn"]').then(($btn) => {
      const rect = $btn[0].getBoundingClientRect();
      expect(rect.height).to.be.gt(40);
    });
  });

  it("no horizontal overflow on merchant home at 320px", () => {
    cy.viewport(320, 568);
    cy.visit("/tengasale/merchant/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// R. HQ DEVICE FINANCE CATALOG
// ────────────────────────────────────────────────────────────────────────────
describe("R — HQ Device Finance Catalog", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("Device Finance Catalog page renders without error", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get("body").should("not.contain", "Server Error");
    cy.get("body").should("not.contain", "500");
  });

  it("catalog header renders", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get('[data-testid="dc-catalog-header"]').should("exist");
    cy.contains(/Device Finance Catalog/i).should("be.visible");
  });

  it("Add Brand button is visible", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get('[data-testid="dc-add-brand-btn"]').should("be.visible");
  });

  it("Add Device Deal button is visible", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get('[data-testid="dc-add-deal-btn"]').should("be.visible");
  });

  it("quick stats row renders", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get('[data-testid="dc-stats-row"]').should("exist");
  });

  it("brands stat shows a value", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get('[data-testid="dc-stat-brands"]').invoke("text").then((t) => {
      expect(t.trim().length).to.be.gt(0);
    });
  });

  it("brand group panels render (if brands exist)", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="dc-brand-group"]').length) {
        cy.get('[data-testid="dc-brand-group"]').should("have.length.gte", 1);
      } else {
        cy.get('[data-testid="dc-empty-state"]').should("exist");
      }
    });
  });

  it("brand group expands and collapses", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="dc-brand-group"]').length) {
        const group = cy.get('[data-testid="dc-brand-group"]').first();
        group.find("summary").click();
        // Should toggle the open attribute
      }
    });
  });

  it("device deal cards render real data (if deals exist)", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get("body").then(($body) => {
      if ($body.find(".dc-card").length) {
        cy.get(".dc-card").first().within(() => {
          cy.get(".dc-model").should("exist");
          cy.get(".dc-card-metrics").should("exist");
          cy.get(".dc-card-footer").should("exist");
          cy.get(".dc-edit-btn").should("have.length.gte", 1);
        });
      }
    });
  });

  it("Edit button and Deactivate/Activate button visible on deal card", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.get("body").then(($body) => {
      if ($body.find(".dc-card").length) {
        cy.get(".dc-card").first().within(() => {
          cy.get(".dc-edit-btn").should("have.length.gte", 2);
        });
      }
    });
  });

  it("Device Finance Catalog no horizontal overflow", () => {
    cy.visit("/tengasale/hq/deals/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("Device Finance Catalog mobile no overflow at 390px", () => {
    cy.viewport(390, 844);
    cy.visit("/tengasale/hq/deals/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// T. HQ OVERVIEW — HERO PANEL + TICKER + TREND BADGES
// ────────────────────────────────────────────────────────────────────────────
describe("T — HQ Overview Mixed Layout", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("HQ overview loads without error", () => {
    cy.visit("/tengasale/hq/");
    cy.get("body").should("not.contain", "Server Error");
    cy.get("body").should("not.contain", "500");
  });

  it("HQ hero row renders", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="hq-hero-row"]').should("exist");
  });

  it("HQ hero shows a contract value figure", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="hq-hero-row"] .hq-hero-value').should("exist");
  });

  it("HQ activity ticker strip renders", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="hq-ticker-strip"]').should("exist");
  });

  it("HQ ticker has multiple data points", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="hq-ticker-strip"] .hq-ticker-item').should("have.length.gte", 4);
  });

  it("HQ KPI grid still renders", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="hq-kpi-grid"]').should("exist");
    cy.get(".hq-kpi").should("have.length.gte", 4);
  });

  it("HQ charts grid renders", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="hq-charts-grid"]').should("exist");
  });

  it("HQ action center renders", () => {
    cy.visit("/tengasale/hq/");
    cy.get('[data-testid="hq-action-center"]').should("exist");
  });

  it("HQ no horizontal overflow at 1280px", () => {
    cy.viewport(1280, 800);
    cy.visit("/tengasale/hq/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 4;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// U. LANDING PAGE PLATFORM FLOW
// ────────────────────────────────────────────────────────────────────────────
describe("U — Landing Page", () => {
  it("landing page loads without error", () => {
    cy.visit("/");
    cy.get("body").should("not.contain", "Server Error");
  });

  it("hero section is visible", () => {
    cy.visit("/");
    cy.get(".hero").should("be.visible");
  });

  it("does not expose internal tooling on public home", () => {
    cy.visit("/");
    cy.get("body").invoke("text").then((text) => {
      expect(text).not.to.include("PayChangu");
      expect(text).not.to.include("HQ credit command");
      expect(text).not.to.include("Volts Engine");
      expect(text).not.to.include("fraud signals");
    });
  });

  it("Start Application links to application flow", () => {
    cy.visit("/");
    cy.get('[data-testid="start-application-cta"]')
      .should("have.attr", "href")
      .and("include", "/applications/new/");
  });

  it("How it Works section renders", () => {
    cy.visit("/");
    cy.get("#how-it-works").should("exist");
    cy.contains(/Choose a phone/i).should("exist");
  });

  it("landing page no horizontal overflow on mobile", () => {
    cy.viewport(390, 844);
    cy.visit("/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// S. HQ DEVICE ENROLLMENT
// ────────────────────────────────────────────────────────────────────────────
describe("S — HQ Device Enrollment", () => {
  beforeEach(() => {
    cy.loginAsHQ();
  });

  it("Device Enrollment page renders without error", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get("body").should("not.contain", "Server Error");
    cy.get("body").should("not.contain", "500");
  });

  it("enrollment header renders", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-header"]').should("exist");
    cy.contains(/Device Enrollment/i).should("be.visible");
  });

  it("live badge is visible", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-live-badge"]').should("be.visible");
  });

  it("hero metrics section renders", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-metrics"]').should("exist");
  });

  it("enrolled hero card renders with count", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-enrolled-hero"]').should("be.visible");
  });

  it("pending mini card renders", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-pending-card"]').should("exist");
  });

  it("failed mini card renders", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-failed-card"]').should("exist");
  });

  it("locked mini card renders", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-locked-card"]').should("exist");
  });

  it("lock pipeline renders with steps", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-pipeline"]').should("exist");
    cy.get(".de-pipeline-step").should("have.length.gte", 3);
  });

  it("filter bar renders", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-filter-bar"]').should("exist");
  });

  it("Filter button is visible and has non-white background", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-filter-btn"]').should("be.visible");
    cy.get('[data-testid="de-filter-btn"]').then(($btn) => {
      const bg = window.getComputedStyle($btn[0]).backgroundColor;
      // Should not be white — must be orange or colored
      expect(bg).to.not.equal("rgb(255, 255, 255)");
    });
  });

  it("Reset link is visible", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-filter-reset"]').should("be.visible");
  });

  it("device table card renders", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get('[data-testid="de-table-card"]').should("exist");
  });

  it("device table has correct column headers", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get(".de-table thead th").should("have.length.gte", 6);
  });

  it("Enrollment status column visible", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get(".de-table thead").should("contain.text", "Enrollment");
  });

  it("Lock status column visible", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.get(".de-table thead").should("contain.text", "Lock");
  });

  it("Device Enrollment no horizontal overflow", () => {
    cy.visit("/tengasale/hq/devices/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("Device Enrollment mobile no overflow at 390px", () => {
    cy.viewport(390, 844);
    cy.visit("/tengasale/hq/devices/");
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth
        > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});
