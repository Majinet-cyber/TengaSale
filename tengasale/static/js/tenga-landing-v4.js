const phoneOffersNode = document.getElementById("publicPhoneOffers");
const PHONE_OFFERS = phoneOffersNode ? JSON.parse(phoneOffersNode.textContent) : [];
const money = value => `MWK ${new Intl.NumberFormat("en-US").format(value)}`;
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const phoneGrid = document.getElementById("phoneGrid");
const pageProgress = document.getElementById("pageProgress");
const header = document.querySelector(".site-header");

function renderPhones() {
  if (!phoneGrid) return;
  const staticUrl = path => path.startsWith("/") || /^https?:\/\//.test(path) ? path : `${phoneGrid.dataset.staticPrefix}${path}`;
  phoneGrid.innerHTML = PHONE_OFFERS.map((phone, index) => {
    const hasPricing = Number(phone.deposit) > 0 && ["daily", "weekly", "monthly"].every(cadence => Number(phone.payments?.[cadence]) > 0);
    const pricing = hasPricing ? `
        <div class="phone-pricing" data-testid="phone-pricing">
          <div class="phone-pricing__selector" role="radiogroup" aria-label="Payment schedule for ${escapeHtml(phone.name)}">
            ${["daily", "weekly", "monthly"].map(cadence => `<button type="button" role="radio" class="phone-pricing__rhythm${cadence === "daily" ? " active" : ""}" data-cadence="${cadence}" data-amount="${Number(phone.payments[cadence])}" aria-checked="${cadence === "daily"}" tabindex="${cadence === "daily" ? "0" : "-1"}">${cadence}</button>`).join("")}
          </div>
          <div class="phone-pricing__selected" aria-live="polite"><strong>${money(phone.payments.daily)}</strong><span>per day</span></div>
          <div class="phone-pricing__deposit"><span>Deposit</span><strong>${money(phone.deposit)}</strong></div>
        </div>` : `
        <div class="phone-pricing phone-pricing--unavailable" data-testid="phone-pricing-unavailable">
          <span>Payment plan</span><strong>Pricing available during application</strong>
        </div>`;
    const visual = phone.image ? `<img class="catalogue-phone-photo" src="${escapeHtml(staticUrl(phone.image))}" alt="${escapeHtml(phone.image_alt || phone.name)}" loading="lazy" width="205" height="260">` : '<div class="css-phone" aria-hidden="true"><div class="css-screen"></div></div>';
    return `
    <article class="phone-card reveal visible" data-phone-brand="${escapeHtml(phone.brand)}" data-is-demo="${Boolean(phone.is_demo)}">
      <div class="phone-visual">${visual}</div>
      <div class="phone-body">
        <div class="phone-top"><div>
          <span class="phone-brand">${phone.logo ? `<img src="${escapeHtml(staticUrl(phone.logo))}" alt="${escapeHtml(phone.brand)}" width="96" height="28">` : escapeHtml(phone.brand)}</span>
          <h3>${escapeHtml(phone.name)}</h3><div class="spec">${escapeHtml(phone.spec)}</div>
        </div></div>
        ${phone.image_note ? `<p class="phone-image-note">${escapeHtml(phone.image_note)}</p>` : ""}
        ${pricing}
        ${phone.is_demo ? '<p class="phone-plan-note">Illustrative plan · Final pricing confirmed during application.</p>' : ""}
        <button class="card-button button" type="button" data-phone-index="${index}">Choose ${escapeHtml(phone.name)}</button>
      </div>
    </article>`;
  }).join("");

  phoneGrid.querySelectorAll(".card-button").forEach(button => {
    button.addEventListener("click", () => {
      const phone = PHONE_OFFERS[Number(button.dataset.phoneIndex)];
      const selection = document.getElementById("selectedPhone");
      const supportLink = document.getElementById("selectedPhoneSupport");
      selection.textContent = `Interested in the ${phone.name}? Ask us about this phone or start an application.`;
      selection.hidden = false;
      supportLink.hidden = false;
      supportLink.dataset.supportSubject = `Phone enquiry: ${phone.name}`;
      document.getElementById("apply").scrollIntoView({ behavior: "smooth" });
    });
  });

  phoneGrid.querySelectorAll(".phone-pricing__selector").forEach(selector => {
    const buttons = [...selector.querySelectorAll(".phone-pricing__rhythm")];
    const selectCadence = button => {
      buttons.forEach(item => {
        const selected = item === button;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-checked", String(selected));
        item.tabIndex = selected ? 0 : -1;
      });
      const pricing = selector.closest(".phone-pricing");
      pricing.querySelector(".phone-pricing__selected strong").textContent = money(Number(button.dataset.amount));
      const unit = { daily: "day", weekly: "week", monthly: "month" }[button.dataset.cadence];
      pricing.querySelector(".phone-pricing__selected span").textContent = `per ${unit}`;
    };
    selector.addEventListener("click", event => {
      const button = event.target.closest(".phone-pricing__rhythm");
      if (button) selectCadence(button);
    });
    selector.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = buttons.findIndex(button => button.getAttribute("aria-checked") === "true");
      const step = ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1;
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + step + buttons.length) % buttons.length;
      selectCadence(buttons[next]);
      buttons[next].focus();
    });
  });
}
renderPhones();

function onScroll() {
  const scrollY = window.scrollY;
  const h = document.documentElement.scrollHeight - window.innerHeight;
  pageProgress.style.width = `${h > 0 ? (scrollY / h) * 100 : 0}%`;
  header.classList.toggle("scrolled", scrollY > 20);
}
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

const menuButton = document.getElementById("menuButton");
const navLinks = document.getElementById("navLinks");
const closeMenu = () => {
  navLinks.classList.remove("open");
  menuButton.setAttribute("aria-expanded", "false");
  document.body.classList.remove("mobile-menu-open");
};
menuButton.addEventListener("click", () => {
  const open = navLinks.classList.toggle("open");
  menuButton.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("mobile-menu-open", open);
  if (open) navLinks.querySelector("a")?.focus();
});
navLinks.querySelectorAll("a").forEach(link => link.addEventListener("click", closeMenu));
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && navLinks.classList.contains("open")) {
    closeMenu();
    menuButton.focus();
  }
});

const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add("visible");
      observer.unobserve(entry.target);
    }
  });
}, { threshold: .12 });
document.querySelectorAll(".reveal").forEach(el => observer.observe(el));

const deviceStage = document.getElementById("deviceStage");
const heroVisual = document.querySelector(".hero-visual");
if (window.matchMedia("(pointer:fine)").matches) {
  heroVisual.addEventListener("pointermove", event => {
    const box = heroVisual.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width - .5;
    const y = (event.clientY - box.top) / box.height - .5;
    deviceStage.style.transform = `rotateY(${x * 8}deg) rotateX(${-y * 7}deg) translate3d(${x * 6}px, ${y * 6}px, 0)`;
  });
  heroVisual.addEventListener("pointerleave", () => deviceStage.style.transform = "");
}

const lockUi = document.getElementById("lockUi");
const lockTitle = document.getElementById("lockTitle");
const lockBody = document.getElementById("lockBody");
const lockButton = document.getElementById("lockButton");
const unlockConfirmation = document.getElementById("unlockConfirmation");
const resetLockButton = document.getElementById("resetLockButton");

lockButton.addEventListener("click", () => {
  lockButton.disabled = true;
  lockButton.textContent = "Confirming payment…";
  setTimeout(() => {
    lockUi.classList.add("paid");
    lockUi.dataset.state = "unlocked";
    lockUi.setAttribute("aria-hidden", "true");
    unlockConfirmation.setAttribute("aria-hidden", "false");
    lockButton.setAttribute("aria-pressed", "true");
    showToast("Payment confirmed", "Device access restored.");
  }, 1200);
});

resetLockButton.addEventListener("click", () => {
  lockUi.classList.remove("paid");
  lockUi.dataset.state = "locked";
  lockUi.removeAttribute("aria-hidden");
  unlockConfirmation.setAttribute("aria-hidden", "true");
  lockButton.textContent = "Simulate payment";
  lockButton.setAttribute("aria-pressed", "false");
  lockButton.disabled = false;
});

const toast = document.getElementById("toast");
let toastTimer;
function showToast(title, text) {
  toast.querySelector("strong").textContent = title;
  toast.querySelector("small").textContent = text;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3400);
}

document.getElementById("year").textContent = new Date().getFullYear();

const supportForm = document.getElementById("supportForm");
const supportCategory = document.getElementById("id_category");
const originatingPage = document.getElementById("originatingPage");

if (originatingPage) originatingPage.value = window.location.href;

document.querySelectorAll("[data-support-category]").forEach(link => {
  link.addEventListener("click", event => {
    const category = link.dataset.supportCategory;
    if (!supportCategory || !category) return;
    event.preventDefault();
    supportCategory.value = category;
    const requestedSubject = link.dataset.supportSubject;
    const subjectInput = document.getElementById("id_subject");
    if (requestedSubject && subjectInput && !subjectInput.value) subjectInput.value = requestedSubject;
    document.getElementById("support").scrollIntoView({ behavior: "smooth" });
    window.setTimeout(() => document.getElementById("id_subject")?.focus(), 550);
  });
});

document.querySelectorAll(".faq-item").forEach(item => {
  const summary = item.querySelector("summary");
  if (!summary) return;
  summary.setAttribute("aria-expanded", String(item.open));
  item.addEventListener("toggle", () => summary.setAttribute("aria-expanded", String(item.open)));
});

const routeState = document.getElementById("landingRouteState");
if (routeState?.dataset.section && !window.location.hash) {
  window.requestAnimationFrame(() => {
    document.getElementById(routeState.dataset.section)?.scrollIntoView({ behavior: "auto" });
  });
}

if (supportForm?.querySelector(".field-error, .form-notice--error")) {
  document.getElementById("support")?.scrollIntoView({ behavior: "auto" });
}


(() => {
  const root = document.getElementById("approvedMarketMap");
  if (!root) return;
  const DATA = {
    malawi:{country:"Malawi",status:"Live",own:56.6,net:18,gap:38.6,year:2023,tone:"Largest gap of the four",contrib:8.2},
    zambia:{country:"Zambia",status:"Next",own:44.6,net:14.3,gap:30.3,year:2018,tone:"Strong expansion case",contrib:6.3},
    zimbabwe:{country:"Zimbabwe",status:"Planned",own:47,net:29.3,gap:17.7,year:2020,tone:"Smaller but attractive gap",contrib:3},
    kenya:{country:"Kenya",status:"Explore",own:53.7,net:35,gap:18.7,year:2024,tone:"East Africa upside",contrib:10.3}
  };
  const order = ["malawi","zambia","zimbabwe","kenya"];
  const targets = [...root.querySelectorAll("[data-market]")];
  const autoButton = document.getElementById("approvedAuto");
  let index = 0;
  let auto = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let timer;
  const spark = values => values.map((value, point) => `${point ? "L" : "M"}${18 + point * 100} ${45 - value / 70 * 34}`).join(" ");
  const show = (key, manual = false) => {
    index = order.indexOf(key);
    const data = DATA[key];
    document.getElementById("approvedCountry").textContent = data.country;
    document.getElementById("approvedStatus").textContent = data.status;
    document.getElementById("approvedGap").textContent = `${Math.round(data.gap)} pts`;
    document.getElementById("approvedTone").textContent = data.tone;
    document.getElementById("approvedOwn").textContent = Math.round(data.own);
    document.getElementById("approvedNet").textContent = Math.round(data.net);
    document.getElementById("approvedOwnBar").style.width = `${data.own}%`;
    document.getElementById("approvedNetBar").style.width = `${data.net}%`;
    document.getElementById("approvedYear").textContent = `ITU · ${data.year}`;
    document.getElementById("approvedContributionLabel").textContent = `${data.country} contribution`;
    document.getElementById("approvedContribution").textContent = `${data.contrib.toFixed(1)}M`;
    document.getElementById("approvedSparkOwn").setAttribute("d", spark([Math.max(8,data.own-12),Math.max(10,data.own-6),data.own]));
    document.getElementById("approvedSparkNet").setAttribute("d", spark([Math.max(4,data.net-6),Math.max(5,data.net-2),data.net]));
    targets.forEach(target => target.classList.toggle("active", target.dataset.market === key));
    if (manual) schedule();
  };
  const schedule = () => { clearInterval(timer); if (auto) timer = setInterval(() => show(order[(index + 1) % order.length]), 3200); };
  targets.forEach(target => target.addEventListener("click", () => show(target.dataset.market, true)));
  autoButton.addEventListener("click", () => { auto = !auto; autoButton.firstChild.textContent = auto ? "Ⅱ " : "▶ "; autoButton.querySelector("span").textContent = auto ? "Auto" : "Paused"; autoButton.setAttribute("aria-label", auto ? "Pause automatic market cycling" : "Play automatic market cycling"); schedule(); });
  root.addEventListener("mouseenter", () => clearInterval(timer));
  root.addEventListener("mouseleave", schedule);
  show("malawi");
  schedule();
})();
