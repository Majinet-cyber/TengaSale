
const phoneOffersNode = document.getElementById("publicPhoneOffers");
const PHONE_OFFERS = phoneOffersNode ? JSON.parse(phoneOffersNode.textContent) : [];
const BRAND_COLORS = {
  Tecno: { glow: "#ddebff", grad: "linear-gradient(135deg,#1868ff,#17c8ff)", accent: "#49d9ff", a: "#193b78", b: "#1f2146" },
  Itel: { glow: "#ffe4e4", grad: "linear-gradient(135deg,#ff7a5c,#ff3232)", accent: "#ff7a5c", a: "#56204a", b: "#1f2146" },
  Samsung: { glow: "#def7f1", grad: "linear-gradient(135deg,#2fd8a4,#2d7bff)", accent: "#4bdcab", a: "#163a43", b: "#2a3c91" },
  "Redmi/Xiaomi": { glow: "#f2e2ff", grad: "linear-gradient(135deg,#8d63ff,#ff74a2)", accent: "#ff6ca6", a: "#171b35", b: "#7a4cff" }
};

const money = value => `MWK ${new Intl.NumberFormat("en-US").format(value)}`;
const cadenceButtons = [...document.querySelectorAll(".cadence-button")];
const phoneGrid = document.getElementById("phoneGrid");
const deviceSelect = document.getElementById("deviceSelect");
const pageProgress = document.getElementById("pageProgress");
const header = document.querySelector(".site-header");
const calculatorPhone = document.getElementById("calculatorPhone");
const calculatorDeposit = document.getElementById("calculatorDeposit");

let activeCadence = "daily";

function renderPhones() {
  if (!phoneGrid) return;
  if (!PHONE_OFFERS.length) {
    phoneGrid.innerHTML = '<p class="catalogue-empty">Current phone plans are being updated. Continue to the application for confirmed availability.</p>';
    return;
  }
  const visualForPhone = phone => {
    const brand = phone.brand.toLowerCase();
    if (brand.includes("redmi") || brand.includes("xiaomi")) return { src: "/static/images/phone-redmi-premium-v1.png", mood: "redmi" };
    if (brand.includes("samsung")) return { src: "/static/images/phone-samsung.png", mood: "ice" };
    if (brand.includes("itel")) return { src: "/static/images/phone-realistic.png", mood: "midnight" };
    if (brand.includes("tecno")) return { src: "/static/images/phone-tecno-camon.png", mood: "aurora" };
    return { src: "/static/images/phone-school.png", mood: "studio" };
  };
  phoneGrid.innerHTML = PHONE_OFFERS.map((phone, index) => {
    const hasPricing = Number(phone.deposit) > 0 && ["daily", "weekly", "monthly"].every(cadence => Number(phone.payments?.[cadence]) > 0);
    const pricing = hasPricing ? `
        <div class="phone-pricing" data-testid="phone-pricing">
          <div class="phone-pricing__selector" role="radiogroup" aria-label="Payment rhythm">
            ${["daily", "weekly", "monthly"].map(cadence => `<button type="button" role="radio" class="phone-pricing__rhythm${cadence === "daily" ? " active" : ""}" data-cadence="${cadence}" data-amount="${phone.payments[cadence]}" aria-checked="${cadence === "daily"}">${cadence}</button>`).join("")}
          </div>
          <div class="phone-pricing__selected" aria-live="polite"><strong>${money(phone.payments.daily)}</strong><span>per day</span></div>
          <div class="phone-pricing__deposit"><span>Deposit</span><strong>${money(phone.deposit)}</strong></div>
        </div>` : `
        <div class="phone-pricing phone-pricing--unavailable" data-testid="phone-pricing-unavailable">
          <span>Payment plan</span><strong>Pricing available during application</strong>
        </div>`;
    return `
    <article class="phone-card reveal visible" data-phone-brand="${phone.brand}" style="
      --soft-glow:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).glow};
      --grad:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).grad};
      --accent:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).accent};
      --screen-a:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).a};
      --screen-b:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).b};
    ">
      <div class="phone-visual">
        <div class="phone-orb"></div>
        <img class="catalogue-phone-photo catalogue-phone-photo--${visualForPhone(phone).mood}" data-phone-visual="${visualForPhone(phone).src}" src="${visualForPhone(phone).src}" alt="${phone.name}">
      </div>
      <div class="phone-body">
        <div class="phone-top">
          <div>
            <span class="phone-brand" aria-label="${phone.brand}">
              <img src="${phone.logo}" alt="" width="96" height="28">
            </span>
            <h3>${phone.name}</h3>
            <div class="spec">${phone.spec}</div>
          </div>
          <span>${phone.status}</span>
        </div>
        ${pricing}
        <button class="card-button" type="button" data-device="${phone.name}">Choose ${phone.name}</button>
      </div>
    </article>
  `;
  }).join("");

  document.querySelectorAll(".card-button").forEach(button => {
    button.addEventListener("click", () => {
      if (deviceSelect) deviceSelect.value = button.dataset.device;
      if (calculatorPhone) calculatorPhone.value = button.dataset.device;
      document.getElementById("apply").scrollIntoView({ behavior: "smooth" });
    });
  });

  document.querySelectorAll(".phone-pricing__selector").forEach(selector => {
    selector.addEventListener("click", event => {
      const button = event.target.closest(".phone-pricing__rhythm");
      if (!button) return;
      const pricing = selector.closest(".phone-pricing");
      selector.querySelectorAll(".phone-pricing__rhythm").forEach(item => {
        const selected = item === button;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-checked", String(selected));
      });
      pricing.querySelector(".phone-pricing__selected strong").textContent = money(Number(button.dataset.amount));
      pricing.querySelector(".phone-pricing__selected span").textContent = `per ${button.dataset.cadence.replace("daily", "day").replace("weekly", "week").replace("monthly", "month")}`;
    });
  });
}

function populateDeviceSelect() {
  if (deviceSelect) deviceSelect.innerHTML = PHONE_OFFERS.map(phone => `<option value="${phone.name}">${phone.name}</option>`).join("");
  if (calculatorPhone) calculatorPhone.innerHTML = PHONE_OFFERS.map(phone => `<option value="${phone.name}">${phone.name}</option>`).join("");
}
function updateCalculator() {
  if (!calculatorPhone || !calculatorDeposit) return;
  const phone = PHONE_OFFERS.find(item => item.name === calculatorPhone.value) || PHONE_OFFERS[0];
  if (!phone) return;
  calculatorDeposit.innerHTML = `<option value="${phone.deposit}">${money(phone.deposit)}</option>`;
  document.getElementById("calculatorModel").textContent = phone.name;
  document.getElementById("calculatorSpec").textContent = phone.spec;
  document.getElementById("calculatorStatus").textContent = phone.status;
  document.getElementById("calculatorDepositValue").textContent = money(phone.deposit);
  document.getElementById("calculatorPaymentLabel").textContent = `${activeCadence.charAt(0).toUpperCase() + activeCadence.slice(1)} payment`;
  document.getElementById("calculatorPaymentValue").textContent = money(phone.payments[activeCadence]);
  if (deviceSelect) deviceSelect.value = phone.name;
}
populateDeviceSelect();
renderPhones();
updateCalculator();
calculatorPhone?.addEventListener("change", updateCalculator);

cadenceButtons.forEach(button => {
  button.addEventListener("click", () => {
    activeCadence = button.dataset.cadence;
    cadenceButtons.forEach(item => item.classList.toggle("active", item === button));
    renderPhones();
    updateCalculator();
  });
});

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


// subtle auto-drift on the metric ribbon for a more alive feel
const ribbon = document.querySelector('.metric-ribbon');
if (ribbon && window.matchMedia('(prefers-reduced-motion: no-preference)').matches) {
  let drift = 0;
  let dir = 1;
  setInterval(() => {
    if (window.innerWidth > 800) return; // manual on mobile
    drift += dir * 120;
    if (drift > ribbon.scrollWidth - ribbon.clientWidth - 40) dir = -1;
    if (drift < 0) dir = 1;
    ribbon.scrollTo({left: Math.max(0, drift), behavior: 'smooth'});
  }, 2800);
}

(() => {
  const root = document.querySelector(".tenga-loop-section");
  if (!root) return;
  const brand = root.querySelector("#loopBrand");
  const model = root.querySelector("#loopModel");
  const condition = root.querySelector("#loopCondition");
  const device = root.querySelector("#loopDevice");
  const cta = root.querySelector("#loopCta");
  const summary = root.querySelector("#loopUseSummary");
  const useButtons = [...root.querySelectorAll(".tenga-loop-use")];
  if (!brand || !model || !condition || !device || !cta || !summary || !useButtons.length) return;
  const catalogue = PHONE_OFFERS.length ? PHONE_OFFERS : [{ brand: "Other", name: "Device not listed" }];
  const brands = [...new Set(catalogue.map(item => item.brand))];
  let selectedUse = "deposit";

  const outcomes = {
    deposit: { title: "Check trade-in eligibility", text: "Tenga Support can help with your next step.", label: "Check Eligibility", category: "trade_in_upgrade" },
    swap: { title: "Talk to Tenga", text: "Ask Tenga Support about available options.", label: "Talk to Tenga", category: "trade_in_upgrade" },
    cash: { title: "Explore Tenga Certified", text: "Ask about certified device availability.", label: "Ask about availability", category: "tenga_certified" }
  };

  function populateModels() {
    const matches = catalogue.filter(item => item.brand === brand.value);
    model.innerHTML = matches.map(item => `<option value="${item.name}">${item.name}</option>`).join("");
    updateDevice();
  }

  function updateDevice() {
    device.textContent = `${model.value || "Device not listed"} · ${condition.value}`;
  }

  function selectOutcome(button) {
    selectedUse = button.dataset.loopUse;
    useButtons.forEach(item => item.classList.toggle("active", item === button));
    const outcome = outcomes[selectedUse];
    summary.querySelector("strong").textContent = outcome.title;
    summary.querySelector("p").textContent = outcome.text;
    cta.textContent = outcome.label;
    cta.href = "#support";
  }

  brand.innerHTML = brands.map(name => `<option value="${name}">${name}</option>`).join("");
  brand.addEventListener("change", populateModels);
  model.addEventListener("change", updateDevice);
  condition.addEventListener("change", updateDevice);
  useButtons.forEach(button => button.addEventListener("click", () => selectOutcome(button)));
  cta.addEventListener("click", event => {
    const outcome = outcomes[selectedUse];
    event.preventDefault();
    const categoryField = document.getElementById("id_category");
    const subjectField = document.getElementById("id_subject");
    const messageField = document.getElementById("id_message");
    if (categoryField) categoryField.value = outcome.category;
    if (subjectField && !subjectField.value) subjectField.value = outcome.title;
    if (messageField && !messageField.value) messageField.value = "I would like Tenga to contact me about trade-in and upgrade eligibility.";
    document.getElementById("support")?.scrollIntoView({ behavior: "smooth" });
    window.setTimeout(() => document.getElementById("id_full_name")?.focus(), 550);
  });
  populateModels();
})();

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
