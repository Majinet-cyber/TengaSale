(function () {
    function initYesNo() {
        document.querySelectorAll("[data-review-yes-no]").forEach(function (component) {
            const input = component.querySelector("[data-review-input]");
            const tick = component.querySelector("[data-review-tick]");
            const buttons = component.querySelectorAll("[data-review-choice]");

            buttons.forEach(function (button) {
                button.addEventListener("click", function () {
                    const value = button.dataset.reviewChoice;
                    input.value = value;
                    buttons.forEach(function (candidate) {
                        candidate.classList.toggle("is-selected", candidate.dataset.reviewChoice === value);
                    });
                    if (tick) {
                        tick.classList.toggle("is-yes", value === "yes");
                        tick.classList.toggle("is-no", value === "no");
                        tick.textContent = value === "yes" ? "✓" : "";
                    }
                });
            });
        });
    }

    function csrfToken(form) {
        const input = form.querySelector("[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    }

    function icon(active) {
        if (active) {
            return '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"></path><path d="M4 9h10a6 6 0 0 1 0 12h-1"></path></svg>';
        }
        return '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 20h9"></path><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>';
    }

    function initCorrections() {
        const modal = document.querySelector("[data-correction-modal]");
        const form = document.querySelector("[data-correction-form]");
        if (!modal || !form) {
            return;
        }

        const fieldInput = form.querySelector("[data-correction-field]");
        const labelInput = form.querySelector("[data-correction-label-input]");
        const sectionInput = form.querySelector("[data-correction-section]");
        const actionInput = form.querySelector("[data-correction-action]");
        const title = form.querySelector("[data-correction-title]");
        const label = form.querySelector("[data-correction-label]");
        const note = form.querySelector("[data-correction-note]");
        const remove = form.querySelector("[data-correction-remove]");
        const submit = form.querySelector("[data-correction-submit]");
        let activeButton = null;

        function close() {
            modal.hidden = true;
            activeButton = null;
        }

        document.querySelectorAll("[data-correction-close]").forEach(function (button) {
            button.addEventListener("click", close);
        });

        document.querySelectorAll("[data-correction-open]").forEach(function (button) {
            button.addEventListener("click", function () {
                activeButton = button;
                const active = button.dataset.correctionActive === "true";
                fieldInput.value = button.dataset.fieldName || "";
                labelInput.value = button.dataset.fieldLabel || "";
                sectionInput.value = button.dataset.fieldSection || "";
                actionInput.value = "mark";
                label.textContent = button.dataset.fieldLabel || "";
                note.value = button.dataset.correctionNote || "";
                title.textContent = active ? "Correction already requested" : "Mark field for correction";
                remove.hidden = !active;
                submit.textContent = active ? "UPDATE NOTE" : "MARK FOR EDIT";
                modal.hidden = false;
                note.focus();
            });
        });

        remove.addEventListener("click", function () {
            actionInput.value = "remove";
            form.requestSubmit();
        });

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            const body = new FormData(form);
            fetch(form.dataset.correctionUrl, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken(form),
                    "X-Requested-With": "XMLHttpRequest"
                },
                body: body
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("Correction could not be saved.");
                    }
                    return response.json();
                })
                .then(function (data) {
                    if (!activeButton) {
                        close();
                        return;
                    }
                    const field = activeButton.closest("[data-review-field]");
                    const active = Boolean(data.active);
                    activeButton.dataset.correctionActive = active ? "true" : "false";
                    activeButton.dataset.correctionNote = data.note || "";
                    activeButton.classList.toggle("review-edit-btn--active", active);
                    activeButton.innerHTML = icon(active);
                    if (field) {
                        field.classList.toggle("review-field--needs-correction", active);
                        let noteNode = field.querySelector(".review-field__note");
                        if (active && data.note) {
                            if (!noteNode) {
                                noteNode = document.createElement("span");
                                noteNode.className = "review-field__note";
                                field.querySelector(".review-field__copy").appendChild(noteNode);
                            }
                            noteNode.textContent = data.note;
                        } else if (noteNode) {
                            noteNode.remove();
                        }
                    }
                    close();
                })
                .catch(function () {
                    submit.textContent = "TRY AGAIN";
                });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initYesNo();
        initCorrections();
    });
})();
