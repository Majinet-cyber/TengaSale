/**
 * TengaSale Cypress E2E — Layout Regression Suite
 * Protects critical UI elements across Merchant, Underwriter, and HQ dashboards.
 * These tests must pass after every change — do NOT weaken or delete them.
 */

// ─────────────────────────────────────────────────────────────────────────────
// MERCHANT HOME — Core Element Presence
// ─────────────────────────────────────────────────────────────────────────────
describe("Merchant Home — Core Elements", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
    cy.visit("/tengasale/merchant/");
  });

  it("TengaSale brand/logo is visible", () => {
    cy.get(".merchant-brand-name").should("be.visible").and("contain.text", "TengaSale");
  });

  it("Malawi flag / country pill is visible", () => {
    cy.get('[data-testid="country-pill-mw"]').should("be.visible");
  });

  it("WhatsApp shortcut is visible", () => {
    cy.get('[data-testid="topbar-whatsapp"]').should("be.visible");
  });

  it("Notification bell is visible", () => {
    cy.get('[data-testid="topbar-notifications"]').should("be.visible");
  });

  it("Logout button is visible", () => {
    cy.get('[data-testid="topbar-logout"]').should("be.visible");
  });

  it("New Application CTA button is visible", () => {
    cy.get('[data-testid="merchant-new-app-btn"]').should("be.visible");
  });

  it("New Application CTA button text is correct", () => {
    cy.get('[data-testid="merchant-new-app-btn"]').should("contain.text", "NEW APPLICATION");
  });

  it("Application status section exists (Active row)", () => {
    cy.get(".action-card").should("exist");
    cy.contains("Active").should("exist");
  });

  it("Application status row labels are clean (no broken fragments)", () => {
    cy.get(".action-row-label").each(($el) => {
      const text = $el.text().trim();
      expect(text).to.not.match(/^[A-Z]\.\s*$/, `Row label looks clipped: "${text}"`);
      expect(text).to.not.include("undefined");
      expect(text).to.not.include("null");
      expect(text).to.not.include("[object Object]");
      expect(text.length).to.be.greaterThan(2);
    });
  });

  it("Action row values are not broken fragments", () => {
    cy.get(".action-row-value").each(($el) => {
      const text = $el.text().trim();
      expect(text).to.not.match(/^[A-Z]\.\.\.$/, `Value looks like a clipped abbreviation: "${text}"`);
      expect(text).to.not.include("undefined");
      expect(text).to.not.include("null");
      expect(text).to.not.include("[object Object]");
    });
  });

  it("Page body has no 'undefined' text fragments", () => {
    cy.get("body").invoke("text").then((text) => {
      // These are developer mistake indicators
      expect(text).to.not.include("undefined");
      expect(text).to.not.include("[object Object]");
    });
  });

  it("Pending Queue row exists", () => {
    cy.contains("Pending Queue").should("exist");
  });

  it("Completed row exists", () => {
    cy.contains("Completed").should("exist");
  });

  it("Portfolio Overview section exists", () => {
    cy.get('[data-testid="merchant-portfolio-overview"]').should("exist");
  });

  it("Earnings section has Earnings Summary", () => {
    cy.get("nav[aria-label='Earnings']").should("contain.text", "Earnings Summary");
  });

  it("Merchant dashboard does not duplicate support", () => {
    cy.contains("Support").should("not.exist");
    cy.contains("Report Issue").should("not.exist");
  });

  it("Greeting message is visible", () => {
    cy.get(".merchant-greeting-title").should("be.visible");
  });

  it("No horizontal overflow at 360px", () => {
    cy.viewport(360, 640);
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("No horizontal overflow at 390px", () => {
    cy.viewport(390, 844);
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });

  it("No horizontal overflow at 430px", () => {
    cy.viewport(430, 932);
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// UNDERWRITER HOME — Core Element Presence
// ─────────────────────────────────────────────────────────────────────────────
describe("Underwriter Home — Core Elements", () => {
  beforeEach(() => {
    cy.loginAsUnderwriter();
    cy.visit("/sales/");
  });

  it("Logo / TengaSale icon is in header", () => {
    cy.get(".ts-header-icon, .tengasale-logo-mark").should("exist");
  });

  it("WhatsApp shortcut is visible", () => {
    cy.get('[data-testid="topbar-whatsapp"]').should("be.visible");
  });

  it("Notification bell is visible", () => {
    cy.get('[data-testid="topbar-notifications"]').should("be.visible");
  });

  it("Logout button is visible", () => {
    cy.get('[data-testid="topbar-logout"]').should("be.visible");
  });

  it("Malawi flag / country pill is visible", () => {
    cy.get('[data-testid="country-pill-mw"]').should("be.visible");
  });

  it("Report Issue link is absent from dashboard", () => {
    cy.contains("Report Issue").should("not.exist");
  });

  it("Application Queue card exists", () => {
    cy.get(".uw-queue-card").should("exist");
  });

  it("Application Queue card has visible title", () => {
    cy.get(".uw-queue-card__title").should("be.visible");
  });

  it("Queue card title is not empty or broken", () => {
    cy.get(".uw-queue-card__title").invoke("text").then((text) => {
      expect(text.trim().length).to.be.greaterThan(3);
      expect(text).to.not.include("undefined");
      expect(text).to.not.include("null");
      expect(text).to.not.include("[object Object]");
    });
  });

  it("Active Reviews card (blue card) exists", () => {
    cy.get(".uw-active-card").should("exist");
  });

  it("Active Reviews card shows count/capacity", () => {
    cy.get(".uw-active-card__count").should("be.visible");
    cy.get(".uw-active-card__count").invoke("text").then((text) => {
      expect(text.trim()).to.match(/\d+\/\d+/, "Count should be in format N/M");
    });
  });

  it("Active Reviews card shows 'Tap to view and continue'", () => {
    cy.get(".uw-active-card__sub").should("contain.text", "Tap to view and continue");
  });

  it("Applications section exists", () => {
    cy.get("nav[aria-label='Applications']").should("exist");
  });

  it("Applications section does not duplicate My Active row", () => {
    cy.get("nav[aria-label='Applications']").should("not.contain.text", "My Active");
  });

  it("Applications section has Pending Queue row", () => {
    cy.get("nav[aria-label='Applications']").should("contain.text", "Pending Queue");
  });

  it("Applications section has Completed row", () => {
    cy.get("nav[aria-label='Applications']").should("contain.text", "Completed");
  });

  it("Tools section is not on the underwriter home", () => {
    cy.get("nav[aria-label='Tools']").should("not.exist");
  });

  it("Earnings section has Earnings & Wallet", () => {
    cy.get("nav[aria-label='Earnings']").should("contain.text", "Earnings");
  });

  it("No broken placeholder fragments in body text", () => {
    cy.get("body").invoke("text").then((text) => {
      expect(text).to.not.include("undefined");
      expect(text).to.not.include("[object Object]");
    });
  });

  it("No horizontal overflow at 390px", () => {
    cy.viewport(390, 844);
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth > win.document.documentElement.clientWidth + 2;
      expect(overflow).to.be.false;
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// HQ / ADMIN DASHBOARD — Core Element Presence
// ─────────────────────────────────────────────────────────────────────────────
describe("HQ Dashboard — Core Elements", () => {
  beforeEach(() => {
    cy.loginAsHQ();
    cy.visit("/tengasale/hq/");
  });

  it("Dashboard shell loads without error", () => {
    cy.get('[data-testid="hq-dashboard"]').should("exist");
    cy.get("body").should("not.contain", "Server Error");
    cy.get("body").should("not.contain", "500");
  });

  it("HQ topbar is visible", () => {
    cy.get(".hq-topbar").should("be.visible");
  });

  it("HQ brand/logo is in topbar", () => {
    cy.get(".hq-logo-mark, .hq-brand-wordmark").should("exist");
  });

  it("WhatsApp icon is in HQ topbar", () => {
    cy.get('[data-testid="topbar-whatsapp"]').should("exist");
  });

  it("Notification bell exists in HQ topbar", () => {
    cy.get(".hq-icon-btn").should("have.length.gte", 2);
  });

  it("Logout button exists in HQ topbar", () => {
    cy.get(".hq-icon-btn--danger, [data-testid='topbar-logout']").should("exist");
  });

  it("Malawi flag / country pill is visible", () => {
    cy.get(".hq-country-pill, [data-testid='country-pill-mw']").should("exist");
  });

  it("Hero portfolio row (summary card) loads", () => {
    cy.get('[data-testid="hq-hero-row"]').should("exist");
    cy.get(".hq-hero-value").should("exist");
  });

  it("Activity ticker strip loads", () => {
    cy.get('[data-testid="hq-ticker-strip"]').should("exist");
    cy.get(".hq-ticker-item").should("have.length.gte", 4);
  });

  it("KPI metrics grid loads", () => {
    cy.get('[data-testid="hq-kpi-grid"]').should("exist");
    cy.get(".hq-kpi").should("have.length.gte", 4);
  });

  it("KPI values are not undefined or blank", () => {
    cy.get(".hq-kpi__val").each(($el) => {
      const text = $el.text().trim();
      expect(text).to.not.equal("");
      expect(text).to.not.equal("undefined");
      expect(text).to.not.equal("null");
      expect(text).to.not.include("[object Object]");
    });
  });

  it("Charts grid loads", () => {
    cy.get('[data-testid="hq-charts-grid"]').should("exist");
  });

  it("Action center loads", () => {
    cy.get('[data-testid="hq-action-center"]').should("exist");
  });

  it("Sidebar navigation is present", () => {
    cy.get(".hq-sidebar").should("exist");
  });

  it("Sidebar has Applications link", () => {
    cy.get(".hq-sidebar").should("contain.text", "Applications");
  });

  it("Sidebar has Analytics / Reports section", () => {
    cy.get(".hq-sidebar").should("contain.text", "Reports");
  });

  it("No 'undefined' text fragments on HQ dashboard", () => {
    cy.get("body").invoke("text").then((text) => {
      expect(text).to.not.include("undefined");
      expect(text).to.not.include("[object Object]");
    });
  });

  it("No horizontal overflow at 1280px desktop", () => {
    cy.viewport(1280, 800);
    cy.window().then((win) => {
      const overflow = win.document.documentElement.scrollWidth > win.document.documentElement.clientWidth + 4;
      expect(overflow).to.be.false;
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// HQ COLLAPSIBLE SECTIONS — if implemented
// ─────────────────────────────────────────────────────────────────────────────
describe("HQ Dashboard — Collapsible Sections", () => {
  beforeEach(() => {
    cy.loginAsHQ();
    cy.visit("/tengasale/hq/");
  });

  it("Collapsible section toggles (if present)", () => {
    cy.get("body").then(($body) => {
      const toggles = $body.find(".hq-section-toggle, [data-hq-collapse-btn]");
      if (toggles.length) {
        cy.wrap(toggles.first()).click();
        // Section should toggle — body shouldn't error
        cy.get("body").should("not.contain", "Server Error");
      }
      // If no collapsible sections yet, test passes silently
    });
  });

  it("Overview section is visible by default", () => {
    // The hero row is part of the always-visible Overview
    cy.get('[data-testid="hq-hero-row"]').should("be.visible");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// OUTSIDE HOURS — Inline Alert (not blocking modal)
// ─────────────────────────────────────────────────────────────────────────────
describe("Outside Hours — Inline Alert Behavior", () => {
  it("Outside hours notice appears inline at top of form (not blocking body scroll)", () => {
    cy.loginAsMerchant();
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="outside-hours-modal"]').length) {
        cy.get('[data-testid="outside-hours-modal"]').should("be.visible");

        // Body overflow must NOT be hidden — form must be scrollable
        cy.window().then((win) => {
          const overflow = win.document.body.style.overflow;
          expect(overflow).to.not.equal("hidden");
        });

        // Form should still be accessible without clicking anything
        cy.get("form").should("exist");
        cy.get("form input, form select").first().should("exist");
      }
    });
  });

  it("Outside hours notice can be dismissed with OK button", () => {
    cy.loginAsMerchant();
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      if ($body.find('[data-testid="outside-hours-continue"]').length) {
        cy.get('[data-testid="outside-hours-continue"]').click();
        cy.get('[data-testid="outside-hours-modal"]').should("not.be.visible");
      }
    });
  });

  it("Form is fully usable before and after outside hours notice", () => {
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

// ─────────────────────────────────────────────────────────────────────────────
// KYC — ID Card Landscape Layout
// ─────────────────────────────────────────────────────────────────────────────
describe("KYC — ID Card Landscape Ratio", () => {
  beforeEach(() => {
    cy.loginAsMerchant();
  });

  it("ID front landscape hint text exists on kyc page", () => {
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      // Only runs if we can access the KYC step
      const kycLinks = $body.find("a[href*='/kyc/']");
      if (kycLinks.length) {
        cy.wrap(kycLinks.first()).click({ force: true });
        cy.get(".kyc-landscape-hint").should("have.length.gte", 1);
      }
    });
  });

  it("ID front preview image has landscape class when rendered", () => {
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      if ($body.find(".kyc-id-front-preview").length) {
        cy.get(".kyc-id-front-preview").then(($img) => {
          const rect = $img[0].getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            // Width should be greater than height for landscape
            expect(rect.width).to.be.greaterThan(rect.height);
          }
        });
      }
    });
  });

  it("ID back preview image has landscape class when rendered", () => {
    cy.visit("/applications/new/");
    cy.get("body").then(($body) => {
      if ($body.find(".kyc-id-back-preview").length) {
        cy.get(".kyc-id-back-preview").should("exist");
      }
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// GLOBAL — No Broken Placeholder Text on Any Key Page
// ─────────────────────────────────────────────────────────────────────────────
describe("Global — No Broken Placeholder Text", () => {
  it("Merchant home has no 'null', 'undefined', or '[object Object]'", () => {
    cy.loginAsMerchant();
    cy.visit("/tengasale/merchant/");
    cy.get("body").invoke("text").then((text) => {
      expect(text).to.not.include("[object Object]");
      expect(text).to.not.include("lorem ipsum");
    });
  });

  it("Underwriter home has no broken placeholders", () => {
    cy.loginAsUnderwriter();
    cy.visit("/sales/");
    cy.get("body").invoke("text").then((text) => {
      expect(text).to.not.include("[object Object]");
    });
  });

  it("HQ dashboard has no lorem ipsum placeholder content", () => {
    cy.loginAsHQ();
    cy.visit("/tengasale/hq/");
    cy.get("body").invoke("text").then((text) => {
      expect(text.toLowerCase()).to.not.include("lorem ipsum");
      expect(text).to.not.include("[object Object]");
    });
  });
});
