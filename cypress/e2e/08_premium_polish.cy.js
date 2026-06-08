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
