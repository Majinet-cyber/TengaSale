
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
  if (!PHONE_OFFERS.length) {
    phoneGrid.innerHTML = '<p class="catalogue-empty">Current phone plans are being updated. Continue to the application for confirmed availability.</p>';
    return;
  }
  phoneGrid.innerHTML = PHONE_OFFERS.map(phone => `
    <article class="phone-card reveal visible" style="
      --soft-glow:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).glow};
      --grad:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).grad};
      --accent:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).accent};
      --screen-a:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).a};
      --screen-b:${(BRAND_COLORS[phone.brand] || BRAND_COLORS.Tecno).b};
    ">
      <div class="phone-visual">
        <div class="phone-orb"></div>
        <div class="css-phone" aria-hidden="true"><div class="css-screen"></div></div>
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
        <div class="pricing">
          <div class="price-box">
            <small>Deposit</small>
            <strong>${money(phone.deposit)}</strong>
          </div>
          <i></i>
          <div class="price-box payment">
            <small>${activeCadence.charAt(0).toUpperCase() + activeCadence.slice(1)} payment</small>
            <strong>${money(phone.payments[activeCadence])}</strong>
          </div>
        </div>
        <button class="card-button" type="button" data-device="${phone.name}">Choose ${phone.name}</button>
      </div>
    </article>
  `).join("");

  document.querySelectorAll(".card-button").forEach(button => {
    button.addEventListener("click", () => {
      deviceSelect.value = button.dataset.device;
      calculatorPhone.value = button.dataset.device;
      updateCalculator();
      document.getElementById("apply").scrollIntoView({ behavior: "smooth" });
      setTimeout(() => document.querySelector('[name="name"]').focus(), 650);
    });
  });
}

function populateDeviceSelect() {
  deviceSelect.innerHTML = PHONE_OFFERS.map(phone => `<option value="${phone.name}">${phone.name}</option>`).join("");
  calculatorPhone.innerHTML = PHONE_OFFERS.map(phone => `<option value="${phone.name}">${phone.name}</option>`).join("");
}
function updateCalculator() {
  const phone = PHONE_OFFERS.find(item => item.name === calculatorPhone.value) || PHONE_OFFERS[0];
  if (!phone) return;
  calculatorDeposit.innerHTML = `<option value="${phone.deposit}">${money(phone.deposit)}</option>`;
  document.getElementById("calculatorModel").textContent = phone.name;
  document.getElementById("calculatorSpec").textContent = phone.spec;
  document.getElementById("calculatorStatus").textContent = phone.status;
  document.getElementById("calculatorDepositValue").textContent = money(phone.deposit);
  document.getElementById("calculatorPaymentLabel").textContent = `${activeCadence.charAt(0).toUpperCase() + activeCadence.slice(1)} payment`;
  document.getElementById("calculatorPaymentValue").textContent = money(phone.payments[activeCadence]);
  deviceSelect.value = phone.name;
}
populateDeviceSelect();
renderPhones();
updateCalculator();
calculatorPhone.addEventListener("change", updateCalculator);

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
menuButton.addEventListener("click", () => {
  const open = navLinks.classList.toggle("open");
  menuButton.setAttribute("aria-expanded", String(open));
});
navLinks.querySelectorAll("a").forEach(link => link.addEventListener("click", () => {
  navLinks.classList.remove("open");
  menuButton.setAttribute("aria-expanded", "false");
}));

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

lockButton.addEventListener("click", () => {
  lockButton.disabled = true;
  lockButton.textContent = "Confirming payment…";
  setTimeout(() => {
    lockUi.classList.add("paid");
    lockTitle.textContent = "Payment received";
    lockBody.textContent = "Access restored automatically. The device is active again.";
    lockButton.textContent = "Device active";
    showToast("Payment confirmed", "Device access restored.");
    setTimeout(() => {
      lockUi.classList.remove("paid");
      lockTitle.textContent = "Payment reminder";
      lockBody.textContent = "Your payment is due. Pay now through the Tenga payment gateway.";
      lockButton.textContent = "Simulate payment";
      lockButton.disabled = false;
    }, 4200);
  }, 1200);
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
