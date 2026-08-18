describe("Landing regression fixes", () => {
  const mobileViewports = [[360, 800], [390, 844], [412, 915], [430, 932]];

  mobileViewports.forEach(([width, height]) => {
    it(`keeps market intelligence and cards inside ${width}x${height}`, () => {
      cy.viewport(width, height);
      cy.visit("/");
      cy.get("#approvedMarketMap").scrollIntoView().should("be.visible");
      cy.get(".approved-tam").should("be.visible").and("contain", "27.8");
      cy.get(".approved-dock").should("be.visible").and("contain", "Focus market");
      cy.document().then(doc => {
        expect(doc.documentElement.scrollWidth).to.be.at.most(doc.documentElement.clientWidth);
      });
    });
  });

  it("updates the mobile contribution from the shared market state", () => {
    cy.viewport(390, 844);
    cy.visit("/");
    cy.get('.approved-market-bar [data-market="zambia"]').click();
    cy.get("#approvedContributionLabel").should("have.text", "Zambia contribution");
    cy.get("#approvedContribution").should("have.text", "6.3M");
    cy.get("#approvedCountry").should("have.text", "Zambia");
  });

  it("shows one repayment cadence at a time and keeps the deposit stable", () => {
    cy.viewport(390, 844);
    cy.visit("/");
    cy.get("[data-testid=phone-pricing]").first().as("pricing");
    cy.get("@pricing").find('[role="radio"]').should("have.length", 3);
    cy.get("@pricing").find('[role="radio"][aria-checked="true"]').should("have.length", 1).and("contain", "daily");
    cy.get("@pricing").find(".phone-pricing__deposit strong").invoke("text").then(deposit => {
      cy.get("@pricing").find('[data-cadence="weekly"]').click();
      cy.get("@pricing").find('[data-cadence="weekly"]').should("have.attr", "aria-checked", "true");
      cy.get("@pricing").find(".phone-pricing__selected span").should("have.text", "per week");
      cy.get("@pricing").find(".phone-pricing__deposit strong").should("have.text", deposit);
      cy.get("@pricing").find('[data-cadence="monthly"]').click();
      cy.get("@pricing").find(".phone-pricing__selected span").should("have.text", "per month");
      cy.get("@pricing").find('[role="radio"][aria-checked="true"]').should("have.length", 1);
    });
  });

  it("uses the corrected hero phone asset", () => {
    cy.visit("/");
    cy.get(".hero-phone-photo").should("have.attr", "src").and("include", "phone-banking-groceries.png");
    cy.get("body").should("not.contain", "Croseries");
  });
});
