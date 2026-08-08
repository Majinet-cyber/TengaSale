const screens = [
  ["merchant-home", "merchant1", "/tengasale/merchant/"],
  ["underwriter-home", "underwriter1", "/tengasale/underwriter/"],
  ["earnings", "merchant1", "/earnings/?tab=transactions"],
  ["active-list", "underwriter1", "/sales/applications/?tab=active"],
  ["completed-list", "underwriter1", "/sales/applications/?tab=completed"],
  ["quality-control", "hqadmin", "/tengasale/hq/qc/call-recordings/"],
  ["recommerce", "recommerce_ui", "/tengasale/recommerce/intake/"],
  ["hq", "hqadmin", "/tengasale/hq/"],
];

describe("shared Tenga component visual proof", () => {
  for (const [name, username, path] of screens) {
    for (const [viewport, width, height] of [["desktop", 1366, 768], ["mobile", 390, 844]]) {
      it(`${name} ${viewport}`, () => {
        cy.viewport(width, height);
        cy.loginAs(username, "testpass123");
        cy.visit(path);
        cy.get("body").should("be.visible");
        cy.document().then(doc => expect(doc.documentElement.scrollWidth).to.be.at.most(width + 1));
        cy.screenshot(`tenga-component-proof/${name}-${viewport}`, { capture: "viewport" });
      });
    }
  }
});
