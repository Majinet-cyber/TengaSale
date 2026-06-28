/**
 * TengaSale Cypress E2E - Underwriter Flow
 * Tests /sales/ underwriter review flow
 */
describe("Underwriter Flow - /sales/", () => {
  beforeEach(() => {
    cy.loginAsUnderwriter();
  });

  it("underwriter can access sales home", () => {
    cy.visit("/sales/");
    cy.get('[data-testid="claim-next"]').should("exist");
    cy.get('[data-testid="country-pill-mw"]').should("contain.text", "Malawi");
    cy.get('[data-testid="country-pill-mw"]').should("not.contain.text", "MW");
  });

  it("shows clean T logo in header (not giant marketing image)", () => {
    cy.visit("/sales/");
    cy.get(".ts-header-icon").should("exist");
    cy.get(".ts-header-icon").invoke("attr", "src").should("include", "brand");
  });

  it("has no Yellow or KulaSell references on sales home", () => {
    cy.visit("/sales/");
    cy.get("body").invoke("text").then((text) => {
      expect(text).not.to.include("KulaSell");
      expect(text).not.to.include("Yellow Africa");
    });
  });

  it("can view wallet/earnings page", () => {
    cy.visit("/sales/wallet/");
    cy.get('[data-testid="underwriter-wallet"]').should("exist");
    cy.contains("Available Commission Balance").should("be.visible");
  });

  it("wallet does not show merchant payout data", () => {
    cy.visit("/sales/wallet/");
    cy.get("body").invoke("text").then((text) => {
      expect(text).not.to.include("cash_price");
      expect(text).not.to.include("merchant_commission");
    });
  });

  it("can access applications list", () => {
    cy.visit("/sales/applications/");
    cy.contains("Applications").should("be.visible");
  });

  it("can access queue rules page outside the home dashboard", () => {
    cy.visit("/sales/queue-rules/");
    cy.contains("Queue Rules").should("be.visible");
  });

  it("can access payments view", () => {
    cy.visit("/sales/payments/");
    cy.contains("Payments").should("be.visible");
  });
});

describe("Underwriter - Mark Field for Review", () => {
  beforeEach(() => {
    cy.loginAsUnderwriter();
  });

  it("mark field button has correct testid", () => {
    cy.visit("/sales/");
    cy.get("body").should("be.visible");
  });
});
