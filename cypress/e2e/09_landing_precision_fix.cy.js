describe("Tenga landing precision fix", () => {
  beforeEach(() => cy.visit("/"));

  it("keeps the payment reminder and both states on the phone", () => {
    cy.get('[data-testid="payment-simulation-phone"]').scrollIntoView().should("be.visible");
    cy.get('[data-testid="on-device-lock-reminder"]').should("be.visible");
    cy.get("#lockUi").should("have.attr", "data-state", "locked");

    cy.get("#lockButton").click();
    cy.get("#lockUi", { timeout: 3000 }).should("have.attr", "data-state", "unlocked");
    cy.get('[data-testid="unlocked-phone-content"]').should("be.visible");

    cy.get("#resetLockButton").click();
    cy.get("#lockUi").should("have.attr", "data-state", "locked");
  });

  it("ends with a compact footer and no link columns or overflow", () => {
    cy.get(".site-footer").scrollIntoView().should("be.visible");
    cy.get(".site-footer .footer-layout--minimal").should("exist");
    cy.get(".site-footer .footer-group").should("not.exist");
    cy.document().then(doc => {
      expect(doc.documentElement.scrollWidth).to.be.at.most(doc.documentElement.clientWidth + 1);
    });
  });
});
