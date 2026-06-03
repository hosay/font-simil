/* FontMatch — UI interactions */
document.addEventListener("DOMContentLoaded", function () {

    /* --- Toast notification --- */
    var toastEl = null;
    var toastTimeout = null;
    function showToast(msg) {
        if (!toastEl) {
            toastEl = document.createElement("div");
            toastEl.className = "toast";
            document.body.appendChild(toastEl);
        }
        toastEl.textContent = msg;
        toastEl.classList.add("show");
        clearTimeout(toastTimeout);
        toastTimeout = setTimeout(function () {
            toastEl.classList.remove("show");
        }, 2000);
    }

    /* --- Star rating (left-to-right: click star N fills stars 1..N) --- */
    document.querySelectorAll(".star-rating").forEach(function (widget) {
        var queryFont = widget.dataset.query;
        var matchFont = widget.dataset.match;
        var stars = widget.querySelectorAll(".star");
        var feedback = widget.querySelector(".rating-feedback");
        var rated = false;

        /* Hover: highlight 1..hovered */
        stars.forEach(function (star, idx) {
            star.addEventListener("mouseenter", function () {
                if (rated) return;
                stars.forEach(function (s, i) {
                    s.textContent = i <= idx ? "\u2605" : "\u2606";
                    s.style.color = i <= idx ? "#f59e0b" : "";
                });
            });
        });

        widget.addEventListener("mouseleave", function () {
            if (rated) return;
            stars.forEach(function (s) {
                if (!s.classList.contains("active")) {
                    s.textContent = "\u2606";
                    s.style.color = "";
                }
            });
        });

        /* Click: lock rating and show community feedback */
        stars.forEach(function (star, idx) {
            star.addEventListener("click", function () {
                rated = true;
                stars.forEach(function (s, i) {
                    if (i <= idx) {
                        s.classList.add("active");
                        s.textContent = "\u2605";
                    } else {
                        s.classList.remove("active");
                        s.textContent = "\u2606";
                    }
                });

                fetch("/api/scores", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        query_font: queryFont,
                        match_font: matchFont,
                        score: idx + 1
                    })
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (feedback && data.vote_count) {
                        var avg = data.average_score ? data.average_score.toFixed(1) : "?";
                        feedback.textContent = avg + "/5 avg (" + data.vote_count + " vote" + (data.vote_count === 1 ? "" : "s") + ")";
                    }
                });
            });
        });
    });

    /* --- Copy CSS button --- */
    document.querySelectorAll(".copy-css-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var family = btn.dataset.family;
            var css = '<link href="https://fonts.googleapis.com/css2?family=' +
                family.replace(/ /g, "+") + '&display=swap" rel="stylesheet">';
            navigator.clipboard.writeText(css).then(function () {
                btn.textContent = "Copied!";
                btn.classList.add("done");
                showToast("Google Fonts embed code copied to clipboard");
                setTimeout(function () {
                    btn.textContent = "Copy CSS";
                    btn.classList.remove("done");
                }, 2000);
            });
        });
    });

    /* --- Report bad match button --- */
    document.querySelectorAll(".report-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            if (btn.classList.contains("reported")) return;
            fetch("/api/report", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    query_font: btn.dataset.query,
                    match_font: btn.dataset.match
                })
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                btn.textContent = "Flagged";
                btn.classList.add("reported");
                showToast(data.reported ? "Thanks for the feedback!" : "Already flagged");
            });
        });
    });

    /* --- Loading state on upload form submit --- */
    var form = document.getElementById("upload-form");
    if (form) {
        form.addEventListener("submit", function () {
            var btn = document.getElementById("submit-btn");
            if (btn) {
                btn.disabled = true;
                btn.textContent = "Analyzing font\u2026";
            }
        });
    }

    /* --- Drag-and-drop zone --- */
    var dropZone = document.getElementById("drop-zone");
    if (dropZone) {
        var fileInput = dropZone.querySelector("input[type=file]");
        var label = dropZone.querySelector(".drop-label");

        ["dragenter", "dragover"].forEach(function (evt) {
            dropZone.addEventListener(evt, function (e) {
                e.preventDefault();
                dropZone.classList.add("dragover");
            });
        });

        ["dragleave", "drop"].forEach(function (evt) {
            dropZone.addEventListener(evt, function (e) {
                e.preventDefault();
                dropZone.classList.remove("dragover");
            });
        });

        dropZone.addEventListener("drop", function (e) {
            if (e.dataTransfer.files.length) {
                fileInput.files = e.dataTransfer.files;
                label.textContent = e.dataTransfer.files[0].name;
            }
        });

        fileInput.addEventListener("change", function () {
            if (fileInput.files.length) {
                label.textContent = fileInput.files[0].name;
            }
        });
    }

    /* --- Hero upload button (opens file picker, auto-submits) --- */
    var heroUploadBtn = document.getElementById("hero-upload-btn");
    var heroUploadInput = document.getElementById("hero-upload-input");
    var heroUploadForm = document.getElementById("hero-upload-form");
    if (heroUploadBtn && heroUploadInput && heroUploadForm) {
        heroUploadBtn.addEventListener("click", function () {
            if (heroUploadBtn.disabled) return;
            heroUploadInput.click();
        });
        heroUploadInput.addEventListener("change", function () {
            if (heroUploadInput.files.length) {
                heroUploadBtn.disabled = true;
                heroUploadBtn.textContent = "Analyzing font\u2026";
                heroUploadForm.submit();
            }
        });
    }

    /* --- Hero search autocomplete --- */
    var heroSearch = document.getElementById("hero-search");
    var heroResults = document.getElementById("hero-search-results");
    if (heroSearch && heroResults) {
        var heroTimer = null;

        function heroSlugify(str) {
            return str.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        }

        function heroFetchAndShow(q, callback) {
            fetch("/api/browse?q=" + encodeURIComponent(q) + "&per_page=8")
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.fonts || data.fonts.length === 0) {
                        heroResults.innerHTML = '<div class="hero-search-empty">No fonts found. Try <a href="/identify">uploading the file</a>.</div>';
                        heroResults.style.display = "block";
                        if (callback) callback(null);
                        return;
                    }
                    var html = "";
                    data.fonts.forEach(function (f) {
                        var slug = f.slug || heroSlugify(f.family);
                        var catLabel = f.category;
                        var extra = "";
                        if (f.category === "proprietary") {
                            catLabel = "proprietary";
                            extra = ' <span class="hero-search-arrow">&rarr; ' +
                                (f.oss_equivalent || "").replace(/</g, "&lt;") + '</span>';
                        } else if (f.category === "alias") {
                            catLabel = "";
                        }
                        html += '<a href="/similar-to/' + slug +
                            '" class="hero-search-item"><span class="hero-search-name">' +
                            f.family.replace(/</g, "&lt;") + extra +
                            '</span><span class="hero-search-cat">' +
                            catLabel + '</span></a>';
                    });
                    heroResults.innerHTML = html;
                    heroResults.style.display = "block";
                    if (callback) callback(heroResults.querySelector(".hero-search-item"));
                })
                .catch(function () {
                    if (callback) callback(null);
                });
        }

        heroSearch.addEventListener("input", function () {
            clearTimeout(heroTimer);
            var q = heroSearch.value.trim();
            if (q.length < 2) {
                heroResults.innerHTML = "";
                heroResults.style.display = "none";
                return;
            }
            heroTimer = setTimeout(function () {
                heroFetchAndShow(q);
            }, 200);
        });

        /* Navigate to first result immediately — fetch first if needed */
        function heroGoToFirst() {
            var first = heroResults.querySelector(".hero-search-item");
            if (first) {
                first.click();
                return;
            }
            var q = heroSearch.value.trim();
            if (q.length < 2) return;
            clearTimeout(heroTimer);
            heroFetchAndShow(q, function (item) {
                if (item) item.click();
            });
        }

        heroSearch.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                heroGoToFirst();
            }
        });

        /* Search icon click */
        var heroSearchBtn = document.getElementById("hero-search-btn");
        if (heroSearchBtn) {
            heroSearchBtn.addEventListener("click", function () {
                heroGoToFirst();
            });
        }

        var heroWrap = document.querySelector(".hero-search-wrap");
        document.addEventListener("click", function (e) {
            if (!heroWrap.contains(e.target) && !heroResults.contains(e.target)) {
                heroResults.style.display = "none";
            }
        });
    }

    /* --- Corpus browse: search, filter, pagination --- */
    var searchInput = document.getElementById("corpus-search");
    var resultsDiv = document.getElementById("corpus-results");
    var paginationDiv = document.getElementById("browse-pagination");
    var filterBtns = document.querySelectorAll(".filter-btn");

    if (searchInput && resultsDiv) {
        var currentQuery = "";
        var currentCategory = "";
        var currentPage = 1;
        var debounceTimer = null;

        function slugify(str) {
            return str.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        }

        function loadCorpus(query, category, page) {
            currentQuery = query;
            currentCategory = category;
            currentPage = page;
            var url = "/api/browse?q=" + encodeURIComponent(query) +
                "&category=" + encodeURIComponent(category) +
                "&page=" + page + "&per_page=40";

            fetch(url)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.fonts || data.fonts.length === 0) {
                        resultsDiv.innerHTML = '<div class="browse-empty">No fonts found.</div>';
                        paginationDiv.innerHTML = "";
                        return;
                    }
                    var html = "";
                    data.fonts.forEach(function (f) {
                        html += '<a href="/similar-to/' + slugify(f.family) +
                            '" class="pill pill-outline">' +
                            f.family.replace(/</g, "&lt;") + '</a>';
                    });
                    resultsDiv.innerHTML = html;

                    // Pagination
                    var pagHtml = "";
                    if (data.page > 1) {
                        pagHtml += '<button class="btn btn-outline btn-sm" data-page="' +
                            (data.page - 1) + '">Previous</button> ';
                    }
                    if (data.page < data.pages) {
                        pagHtml += '<button class="btn btn-outline btn-sm" data-page="' +
                            (data.page + 1) + '">Next</button>';
                    }
                    if (data.pages > 1) {
                        pagHtml += '<span style="margin-left:.75rem;font-size:.82rem;color:#6b7280">Page ' +
                            data.page + ' of ' + data.pages + '</span>';
                    }
                    paginationDiv.innerHTML = pagHtml;

                    // Bind pagination buttons
                    paginationDiv.querySelectorAll("button[data-page]").forEach(function (b) {
                        b.addEventListener("click", function () {
                            loadCorpus(currentQuery, currentCategory, parseInt(b.dataset.page));
                            document.getElementById("browse").scrollIntoView({behavior: "smooth"});
                        });
                    });
                });
        }

        searchInput.addEventListener("input", function () {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function () {
                loadCorpus(searchInput.value.trim(), currentCategory, 1);
            }, 300);
        });

        filterBtns.forEach(function (btn) {
            btn.addEventListener("click", function () {
                filterBtns.forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                loadCorpus(currentQuery, btn.dataset.category, 1);
            });
        });
    }
});
