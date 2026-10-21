/* OnRecord landing page interactions.
 * Vanilla JS, no dependencies. All animations are CSS driven; this file
 * only wires up observers, anchor scrolling, and reduced motion support. */

document.addEventListener("DOMContentLoaded", function () {
  var root = document.documentElement;

  /* Reduced motion: skip observer driven animation, reveal everything
   * immediately so content is never hidden from the visitor. */
  var reducedMotion = false;
  try {
    reducedMotion =
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (e) {
    reducedMotion = false;
  }
  if (reducedMotion) {
    root.classList.add("reduced-motion");
  }

  /* 1. Scroll reveals ----------------------------------------------------
   * IntersectionObserver adds .is-visible when a .reveal element enters the
   * viewport. CSS owns the masked rise animation. data-delay values are
   * applied as transition-delay for stagger. Each element is revealed
   * once, then unobserved.
   *
   * NOTE: .reveal hides elements with clip-path: inset(0 0 100% 0), and a
   * fully clipped element reports intersectionRatio 0, so observing the
   * clipped element itself would never fire. The observer therefore
   * watches each reveal element's unclipped parent and reveals the
   * grouped children when that parent enters the viewport. */
  function showReveal(el) {
    var delay = el.getAttribute("data-delay");
    if (delay) {
      el.style.transitionDelay = delay;
    }
    el.classList.add("is-visible");
  }
  var revealEls = document.querySelectorAll(".reveal");
  if (revealEls && revealEls.length > 0) {
    if (reducedMotion) {
      for (var i = 0; i < revealEls.length; i++) {
        showReveal(revealEls[i]);
      }
    } else if ("IntersectionObserver" in window) {
      var revealGroups = [];
      function groupForReveal(el) {
        var host = el.parentNode || document.body;
        for (var g = 0; g < revealGroups.length; g++) {
          if (revealGroups[g].host === host) {
            return revealGroups[g];
          }
        }
        var group = { host: host, els: [] };
        revealGroups.push(group);
        return group;
      }
      for (var k = 0; k < revealEls.length; k++) {
        groupForReveal(revealEls[k]).els.push(revealEls[k]);
      }
      var revealObserver = new IntersectionObserver(
        function (entries, observer) {
          for (var j = 0; j < entries.length; j++) {
            var entry = entries[j];
            if (!entry.isIntersecting) {
              continue;
            }
            for (var g = 0; g < revealGroups.length; g++) {
              if (revealGroups[g].host === entry.target) {
                for (var e = 0; e < revealGroups[g].els.length; e++) {
                  showReveal(revealGroups[g].els[e]);
                }
                observer.unobserve(entry.target);
              }
            }
          }
        },
        { threshold: 0, rootMargin: "0px 0px -8% 0px" }
      );
      for (var m = 0; m < revealGroups.length; m++) {
        revealObserver.observe(revealGroups[m].host);
      }
    } else {
      /* No IntersectionObserver support: show everything. */
      for (var n = 0; n < revealEls.length; n++) {
        showReveal(revealEls[n]);
      }
    }
  }

  /* 2. Ticker pause when offscreen ---------------------------------------
   * The ticker track content is pre-duplicated in HTML and animates with
   * CSS keyframes. JS only toggles .paused when the ticker leaves the
   * viewport so the browser can stop repainting it. */
  var ticker = document.querySelector(".ticker");
  if (ticker && "IntersectionObserver" in window) {
    var tickerObserver = new IntersectionObserver(function (entries) {
      for (var j = 0; j < entries.length; j++) {
        if (entries[j].isIntersecting) {
          ticker.classList.remove("paused");
        } else {
          ticker.classList.add("paused");
        }
      }
    });
    tickerObserver.observe(ticker);
  }

  /* 3. Smooth anchor scrolling -------------------------------------------
   * CSS scroll-behavior: smooth covers this in modern browsers. The
   * fallback below only runs when the browser ignores smooth scrolling,
   * which we detect by checking for support. */
  var supportsSmoothScroll = false;
  try {
    supportsSmoothScroll = "scrollBehavior" in document.documentElement.style;
  } catch (e) {
    supportsSmoothScroll = false;
  }
  var navLinks = document.querySelectorAll('a[href^="#"]');
  if (navLinks && navLinks.length > 0 && !supportsSmoothScroll) {
    for (var n = 0; n < navLinks.length; n++) {
      navLinks[n].addEventListener("click", function (event) {
        var hash = this.getAttribute("href");
        if (!hash || hash === "#") {
          return;
        }
        var target = document.querySelector(hash);
        if (!target) {
          return;
        }
        event.preventDefault();
        var top =
          target.getBoundingClientRect().top + window.pageYOffset;
        window.scrollTo(0, top);
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, "", hash);
        }
      });
    }
  }

  /* Active nav link highlighting. Each in-page nav link whose hash
   * matches a section id gets .active while that section is the one most
   * recently scrolled into view. Kept simple: one observer, no offset
   * math. */
  var sectionLinks = [];
  var sectionsById = {};
  var allAnchors = document.querySelectorAll('nav a[href^="#"], header a[href^="#"]');
  if (allAnchors && allAnchors.length > 0 && "IntersectionObserver" in window) {
    for (var s = 0; s < allAnchors.length; s++) {
      var href = allAnchors[s].getAttribute("href");
      if (!href || href === "#" || href.indexOf("#") !== 0) {
        continue;
      }
      var section = document.querySelector(href);
      if (!section) {
        continue;
      }
      sectionLinks.push({ link: allAnchors[s], id: href.slice(1) });
      sectionsById[href.slice(1)] = allAnchors[s];
    }
    if (sectionLinks.length > 0) {
      var sectionObserver = new IntersectionObserver(
        function (entries) {
          for (var j = 0; j < entries.length; j++) {
            var entry = entries[j];
            var link = sectionsById[entry.target.id];
            if (!link) {
              continue;
            }
            if (entry.isIntersecting) {
              for (var q = 0; q < sectionLinks.length; q++) {
                sectionLinks[q].link.classList.remove("active");
              }
              link.classList.add("active");
            }
          }
        },
        { rootMargin: "-40% 0px -55% 0px", threshold: 0 }
      );
      for (var r = 0; r < sectionLinks.length; r++) {
        var sec = document.getElementById(sectionLinks[r].id);
        if (sec) {
          sectionObserver.observe(sec);
        }
      }
    }
  }

  /* 4. Mobile nav overflow -----------------------------------------------
   * Primary fix lives in CSS (horizontal scroll on small screens). JS
   * only adds a hook class when the nav actually overflows so CSS can
   * style that state, e.g. edge fades. */
  function checkNavOverflow() {
    var navs = document.querySelectorAll("nav, .nav-links");
    if (!navs || navs.length === 0) {
      return;
    }
    for (var j = 0; j < navs.length; j++) {
      var nav = navs[j];
      if (nav.scrollWidth > nav.clientWidth + 1) {
        nav.classList.add("overflows");
      } else {
        nav.classList.remove("overflows");
      }
    }
  }
  var navPresent = document.querySelector("nav, .nav-links");
  if (navPresent) {
    checkNavOverflow();
    var resizeTimer = null;
    window.addEventListener("resize", function () {
      if (resizeTimer) {
        window.clearTimeout(resizeTimer);
      }
      resizeTimer = window.setTimeout(checkNavOverflow, 150);
    });
  }
});
