//
//  InputMapper.swift
//  InsightBridge
//
//  Maps physical inputs (Neural Band, temple touchpad, keyboard equivalents)
//  onto SessionManager actions. This file is pure mapping logic: it decides
//  WHAT an input means, SessionManager decides HOW it happens. It holds no
//  capture state, touches no source, and sends nothing to the server itself,
//  which keeps the mapping unit testable without any hardware.
//
//  Neural Band reality check (from the build plan, verified against the
//  toolkit): third party apps do NOT get raw EMG and can NOT define custom
//  gestures. What arrives is a small set of discrete key style events from
//  the band and the glasses d-pad: four directions plus confirm, surfaced
//  to the app as arrow keys and Enter, plus two pinch gestures that also
//  arrive as key events. This mapper is written against exactly that set.
//  Anything fancier (custom pinch patterns, EMG intent) is not available
//  and is deliberately not faked here.
//
//  Where results render: the bridge app is a capture device, not the query
//  UI. A "re-query memory" input tags the moment on the server with source
//  "requery"; the answer renders in the dashboard and, on the Display
//  model, in the lens web app. Nothing in this file pretends the bridge
//  displays query results.
//

import Foundation
import UIKit

// MARK: - Input vocabularies

/// The complete set of discrete inputs the Neural Band and d-pad can
/// deliver to a third party app. pinchPrimary and pinchSecondary are the
/// two pinch gestures; they arrive as key events, identical in kind to
/// `enter`, just from a different physical action.
enum BandKey: Equatable {
    case up
    case down
    case left
    case right
    case enter
    case pinchPrimary
    case pinchSecondary
}

/// Inputs from the glasses temple touchpad. These are input source stubs:
/// the events below describe what the touchpad will deliver once the
/// Display and Neural Band input path is enabled on hardware day. The
/// mapping is final; only the event delivery wiring is pending hardware.
enum TempleEvent: Equatable {
    case tap
    case longPress
    case swipeForward
    case swipeBack
}

/// What an input resolved to. Handlers return this so tests (and debug
/// overlays) can assert the mapping without a live SessionManager.
enum InputAction: Equatable {
    case tagMoment
    case requery
    case pause
    case resume
    case killSwitch
    case none
}

// MARK: - InputMapper

/// MainActor because every action it dispatches lands on SessionManager,
/// which is MainActor isolated, and because its real callers (key events,
/// touchpad callbacks, UI buttons) all arrive on the main thread anyway.
@MainActor
final class InputMapper {

    /// Weak on purpose: SessionManager owns the app graph, the mapper is a
    /// leaf. If the manager goes away, inputs become safe no-ops.
    weak var sessionManager: SessionManager?

    /// Provided by the dashboard facing layer. Called when the user asks to
    /// re-run the last memory query (BandKey.up). The closure owns whatever
    /// refresh the dashboard needs; the tag on the server (sent separately,
    /// see handleKeyEvent) is what links the query to this moment in the
    /// conversation timeline.
    var onRequery: (() -> Void)?

    init(sessionManager: SessionManager? = nil, onRequery: (() -> Void)? = nil) {
        self.sessionManager = sessionManager
        self.onRequery = onRequery
    }

    // MARK: - Neural Band and d-pad

    /// The band mapping, kept deliberately small and deterministic:
    ///   - pinchPrimary / enter : tag this moment
    ///   - up                   : re-query memory
    ///   - down                 : toggle pause / resume
    ///   - left / right         : reserved, no-op (see below)
    ///   - pinchSecondary       : reserved, no-op (see below)
    @discardableResult
    func handleKeyEvent(_ key: BandKey) -> InputAction {
        guard let manager = sessionManager else { return .none }
        switch key {
        case .pinchPrimary, .enter:
            // The one gesture a wearer can do without looking: mark the
            // moment so it can be found later ("that thing they just said").
            manager.tagMoment(source: "neural-band")
            return .tagMoment

        case .up:
            // Re-query memory. Honest scope: this sends a tagMoment with
            // source "requery" so the server and dashboard know a re-query
            // was requested at this point in the conversation, and it fires
            // the provided onRequery closure. The query itself runs server
            // side and the answer renders on the dashboard or the lens web
            // app, NOT in this bridge app.
            onRequery?()
            manager.tagMoment(source: "requery")
            return .requery

        case .down:
            // Toggle pause / resume based on the live conversation state.
            // Only two states can toggle; anything else is a no-op so a
            // stray press in idle or ended state can never corrupt a session.
            switch manager.conversationState {
            case .recording, .armed:
                manager.pauseSession()
                return .pause
            case .paused:
                manager.resumeSession()
                return .resume
            case .idle, .ended:
                return .none
            }

        case .left, .right:
            // Reserved. Natural future uses: cycle the active speaker chip
            // (left / right through the enrolled names) or step through
            // recent tagged moments. Both need the dashboard side speaker
            // UI to exist first, and a wrong guess here during a live demo
            // is worse than a deliberate no-op, so these stay inert.
            return .none

        case .pinchSecondary:
            // Reserved. Candidate future use: push-to-talk hold equivalent
            // for hands free recording. Not wired for the demo because PTT
            // on the phone button is the deterministic critical path, and a
            // second, less tested talk trigger is a false trigger risk.
            return .none
        }
    }

    /// Keyboard flavoured entry point. On hardware, band and d-pad events
    /// arrive in this shape (arrows plus Enter), and a connected keyboard
    /// produces the same events in the simulator, which is how this mapping
    /// gets exercised without glasses on a desk.
    @discardableResult
    func handleKeyEvent(_ usage: UIKeyboardHIDUsage) -> InputAction {
        switch usage {
        case .keyboardUpArrow:
            return handleKeyEvent(.up)
        case .keyboardDownArrow:
            return handleKeyEvent(.down)
        case .keyboardLeftArrow:
            return handleKeyEvent(.left)
        case .keyboardRightArrow:
            return handleKeyEvent(.right)
        case .keyboardReturnOrEnter, .keypadEnter:
            return handleKeyEvent(.enter)
        default:
            // Every other key is ignored. The band only emits the set above;
            // anything else is some other device and not our business.
            return .none
        }
    }

    // MARK: - Temple touchpad (input source stubs)
    //
    // STUB STATUS: the handlers below are final mapping logic wired to real
    // SessionManager calls, but the event DELIVERY is a stub. The temple
    // touchpad event stream only exists once the Display / Neural Band input
    // path is enabled on the borrowed hardware (hardware day task), at which
    // point the touchpad callback calls handleTempleEvent and nothing else
    // changes. Until then these are reachable from tests and debug buttons.

    @discardableResult
    func handleTempleEvent(_ event: TempleEvent) -> InputAction {
        guard let manager = sessionManager else { return .none }
        switch event {
        case .tap:
            // Same meaning as the primary pinch: tag this moment. Two
            // physical paths, one action ID, so the server cannot tell (and
            // does not need to tell) which surface produced the tag beyond
            // the source string.
            manager.tagMoment(source: "temple-touchpad")
            return .tagMoment

        case .longPress:
            // Deliberate mirror of the on screen GO PHONE-ONLY button: a
            // long press is hard to do by accident, easy to do on purpose
            // while someone is mid sentence, and runs the exact same
            // killSwitch path (which funnels into the one failoverToPhone).
            // No confirmation, same as the button.
            manager.killSwitch()
            return .killSwitch

        case .swipeForward, .swipeBack:
            // No-op stubs. Candidate future uses: swipeForward to request an
            // eager still photo, swipeBack to re-query memory. Both duplicate
            // actions that already have deterministic triggers elsewhere, so
            // for the demo they stay inert rather than half wired.
            return .none
        }
    }
}
