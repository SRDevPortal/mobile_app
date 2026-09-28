/* Keep calendar layout scoped to its route, including browser-back navigation. */
(() => {
    function sync() {
        const active = frappe.get_route()[0] === "doctor-clinical";
        document.body.classList.toggle("ma-calendar-active", active);
        document.body.classList.remove("ma-doctor-portal-active");
    }
    if (frappe.router) frappe.router.on("change", sync);
    $(document).on("page-change", sync);
})();
