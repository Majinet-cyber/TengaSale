const mobileWidths = [360, 375, 390, 412, 430];

function assertViewportIntegrity() {
  cy.document().then((doc) => {
    expect(doc.documentElement.scrollWidth).to.be.at.most(doc.documentElement.clientWidth + 2);
    cy.get("main").then(($main) => {
      $main.find(":input:visible, button:visible, a:visible").each((_, element) => {
        const $element = Cypress.$(element);
        if ($element.closest('[data-testid="stepper-chips-wrap"]').length) return;
        const rect = element.getBoundingClientRect();
        expect(rect.left).to.be.at.least(-1);
        expect(rect.right).to.be.at.most(doc.documentElement.clientWidth + 2);
      });
    });
  });
}

describe("Merchant application mobile viewport integrity", () => {
  beforeEach(() => cy.loginAsMerchant());

  mobileWidths.forEach((width) => {
    it(`keeps customer details inside ${width}px`, () => {
      cy.viewport(width, 844);
      cy.visit("/applications/new/");
      cy.get(".application-flow-page").should("be.visible");
      assertViewportIntegrity();
    });
  });
});

describe("Underwriter review mobile viewport integrity", () => {
  beforeEach(() => cy.loginAsUnderwriter());

  mobileWidths.forEach((width) => {
    it(`keeps an available review inside ${width}px`, () => {
      cy.viewport(width, 844);
      cy.visit("/sales/applications/3/");
      cy.get(".ts-review-page").should("be.visible");
      assertViewportIntegrity();
    });
  });
});
