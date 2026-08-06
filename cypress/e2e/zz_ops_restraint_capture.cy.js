const shots = [
  ["merchant", "merchant1", "/tengasale/merchant/"],
  ["underwriter", "underwriter1", "/tengasale/underwriter/"],
  ["earnings", "merchant1", "/earnings/"],
  ["recommerce", "recommerce_ui", "/tengasale/recommerce/intake/"],
];

describe("restrained operations screenshot gate", () => {
  for (const [name, username, path] of shots) {
    for (const [viewport, width, height] of [["desktop", 1366, 768], ["mobile", 390, 844]]) {
      it(`${name} ${viewport}`, () => {
        cy.viewport(width, height);
        cy.loginAs(username, "testpass123");
        cy.visit(path);
        cy.get("body").should("be.visible");
        cy.document().then(doc => {
          expect(doc.documentElement.scrollWidth).to.be.at.most(width + 1);
        });
        cy.screenshot(`ops-restraint/${name}-${viewport}`, { capture: "viewport" });
      });
    }
  }
});
