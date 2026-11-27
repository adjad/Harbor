// Contract for Customize Harbor (app/Sources/HarborApp/WildcardTabs.swift): the
// persona to tabs mapping, the two-tab limit, the validator and the saved
// choice. Compiled with NotchTabChecks.swift, which calls wildcardTabsChecks.
// Pure logic on a throwaway defaults suite: never touches the person's settings.
import Foundation

func wildcardTabsChecks(_ check: (Bool, String) -> Void) {
    let suite = "harbor.checks.wildcard.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defer { defaults.removePersistentDomain(forName: suite) }

    // Seven top-level answers; every leaf belongs to one, none is lost.
    check(QuaysidePersonaGroup.allCases.count == 7, "seven top-level answers")
    check(QuaysidePersona.allCases.allSatisfy { $0.group.leaves.contains($0) }, "every answer sits in its group")
    check(Set(QuaysidePersona.allCases.map(\.rawValue)).count == QuaysidePersona.allCases.count, "persona ids are unique")
    check(QuaysidePersonaGroup.student.hasSubtypes && QuaysidePersonaGroup.builder.hasSubtypes
          && !QuaysidePersonaGroup.meetings.hasSubtypes && !QuaysidePersonaGroup.basics.hasSubtypes,
          "only Student and I build things ask a second question")
    check(QuaysidePersonaGroup.allCases.allSatisfy { !$0.title.isEmpty && !$0.subtitle.isEmpty }, "every group has words")
    check(QuaysidePersona.allCases.allSatisfy { !$0.title.isEmpty }, "every answer has a title")

    // Persona to tabs: at most two, only Meet or Study, no repeats. This Intel
    // build has no Ideate tab, so a builder or creator starts with none.
    let expected: [(QuaysidePersona, [NotchTab])] = [
        (.studentHighSchool, [.study]),
        (.studentCollege, [.study]),
        (.studentGrad, [.study, .meet]),
        (.builderUI, []),
        (.builderDebug, []),
        (.builderLearn, [.study]),
        (.builderGeneral, []),
        (.builderElse, []),
        (.meetings, [.meet]),
        (.manager, [.meet]),
        (.creator, []),
        (.home, []),
        (.basics, []),
    ]
    check(expected.count == QuaysidePersona.allCases.count, "every answer is mapped")
    for (persona, tabs) in expected {
        check(persona.suggestedTabs == tabs, "\(persona) suggests \(tabs)")
        check(persona.suggestedTabs.count <= 2, "\(persona) suggests two tabs at most")
        check(persona.suggestedTabs.allSatisfy(WildcardTabs.candidates.contains), "\(persona) suggests only wildcard tabs")
    }
    check(WildcardTabs.candidates == [.study, .meet], "Study and Meet are the only wildcards")

    // The validator: wildcard tabs only, once each, two at most, in order.
    check(WildcardTabs.validated([]) == [], "none is allowed")
    check(WildcardTabs.validated([.study, .study, .meet]) == [.study, .meet], "dedupe, in order")
    check(WildcardTabs.validated([.ask, .brief, .day, .research, .meet]) == [.meet], "Ask, Brief, Day and Research never join")
    check(WildcardTabs.validated([.meet, .study]) == [.meet, .study], "the order given is kept")

    // Change: tick and untick.
    check(WildcardTabs.toggled(.meet, in: []) == [.meet], "a free tab joins")
    check(WildcardTabs.toggled(.study, in: [.meet]) == [.meet, .study], "a second joins at the end")
    check(WildcardTabs.canAdd(.study, to: [.meet]) && !WildcardTabs.canAdd(.meet, to: [.meet]), "canAdd refuses a repeat")
    check(WildcardTabs.toggled(.meet, in: [.meet, .study]) == [.study], "a chosen tab leaves")
    check(WildcardTabs.toggled(.ask, in: [.study]) == [.study], "a fixed tab can't be toggled")

    // The words: title, subtitle, state line and count for 0, 1 and 2 picked.
    check(WildcardTabs.rowTitle == "Extra tabs", "the row is called Extra tabs")
    check(WildcardTabs.rowSubtitle == "Add up to 2 tabs next to Ask, Brief and Day", "the subtitle says what it does and the limit")
    check(WildcardTabs.rowSubtitle.contains("\(WildcardTabs.maxCount)"), "the subtitle's number is the limit")
    check(WildcardTabs.stateLine([]) == "None added yet", "state line: none")
    check(WildcardTabs.stateLine([.meet]) == "Meet is on", "state line: one")
    check(WildcardTabs.stateLine([.study, .meet]) == "Study and Meet are on", "state line: two, in order")
    check(WildcardTabs.stateLine([.meet, .study]) == "Meet and Study are on", "state line follows the saved order")
    check(WildcardTabs.countText([]) == "0 of 2" && WildcardTabs.countText([.meet]) == "1 of 2"
          && WildcardTabs.countText([.meet, .study]) == "2 of 2", "count: 0 of 2, 1 of 2, 2 of 2")
    check(WildcardTabs.blockedNote(for: .meet) == "Remove one to add Meet"
          && WildcardTabs.blockedNote(for: .study) == "Remove one to add Study", "blocked note names the tab")
    check(WildcardTabs.chipValue(.meet, in: [.meet]) == "on" && WildcardTabs.chipValue(.study, in: [.meet]) == "off", "chips say on or off")
    check(WildcardTabs.chipHint(.meet, in: [.meet]) == "Removes Meet from the tab strip"
          && WildcardTabs.chipHint(.study, in: [.meet]) == "Adds Study to the tab strip", "chip hints say what a press does")

    // Pressing: with two wildcards on Intel the limit never refuses one.
    check(WildcardTabs.press(.study, in: []) == WildcardTabs.Press(tabs: [.study], blockedNote: nil), "press adds when there is room")
    check(WildcardTabs.press(.meet, in: [.study]) == WildcardTabs.Press(tabs: [.study, .meet], blockedNote: nil), "the second joins at the end")
    let afterRemove = WildcardTabs.press(.study, in: [.study, .meet])
    check(afterRemove == WildcardTabs.Press(tabs: [.meet], blockedNote: nil), "pressing a picked tab removes it")
    let afterAdd = WildcardTabs.press(.study, in: afterRemove.tabs)
    check(afterAdd == WildcardTabs.Press(tabs: [.meet, .study], blockedNote: nil), "after removing one, adding works and order is kept")
    check(WildcardTabs.press(.ask, in: [.study]) == WildcardTabs.Press(tabs: [.study], blockedNote: nil), "a fixed tab is ignored without a note")
    check(WildcardTabs.stateLine(afterAdd.tabs) == "Meet and Study are on", "the state line follows the press")

    // The saved choice: unset keeps Meet and Study; an empty list is a real choice.
    check(WildcardTabs.chosen(defaults: defaults) == [.meet, .study] && !WildcardTabs.wasChosen(defaults: defaults),
          "never chosen: Meet and Study, so nothing changes for an existing install")
    check(WildcardTabs.set([.study, .meet, .study], defaults: defaults) == [.study, .meet]
          && WildcardTabs.chosen(defaults: defaults) == [.study, .meet] && WildcardTabs.wasChosen(defaults: defaults),
          "a choice is validated, saved and read back in order")
    check(defaults.stringArray(forKey: WildcardTabs.key) == ["study", "meet"], "saved as ids, not numbers")
    WildcardTabs.set([], defaults: defaults)
    check(WildcardTabs.chosen(defaults: defaults) == [] && WildcardTabs.wasChosen(defaults: defaults), "none chosen stays none")
    defaults.set(["study", "ideate", "nonsense", "study", "ask"], forKey: WildcardTabs.key)
    check(WildcardTabs.chosen(defaults: defaults) == [.study], "unknown ids (an Apple silicon Ideate choice) are dropped")
    defaults.set("study", forKey: WildcardTabs.key)
    check(WildcardTabs.chosen(defaults: defaults) == [.meet, .study], "a value of the wrong type counts as never chosen")
    check(WildcardTabs.key == "harbor.wildcardTabs", "the saved key is the one the plan names")

    // The persona store applies the tabs.
    defaults.removePersistentDomain(forName: suite)
    check(QuaysidePersonaStore.persona(defaults: defaults) == nil, "no persona until chosen")
    QuaysidePersonaStore.choose(.builderDebug, defaults: defaults)
    check(QuaysidePersonaStore.persona(defaults: defaults) == .builderDebug
          && WildcardTabs.chosen(defaults: defaults).isEmpty, "a builder gets no wildcard tab on Intel")
    QuaysidePersonaStore.choose(.studentGrad, defaults: defaults)
    check(WildcardTabs.chosen(defaults: defaults) == [.study, .meet], "a grad student gets Study and Meet")
    QuaysidePersonaStore.choose(.basics, defaults: defaults)
    check(WildcardTabs.chosen(defaults: defaults).isEmpty, "Just the basics: Ask, Brief and Day")
    defaults.set("bogus", forKey: QuaysidePersonaStore.personaKey)
    check(QuaysidePersonaStore.persona(defaults: defaults) == nil, "unknown saved ids fall back")

    // Words the step and the tour use.
    check(NotchTab.allCases.allSatisfy { !WildcardTabs.tourLine($0).isEmpty }, "every tab has a tour line")
    check(WildcardTabs.candidates.allSatisfy { !WildcardTabs.summary($0).isEmpty }, "every wildcard has a summary")
    check(WildcardTabs.studyDisclosure.contains("cloud") && WildcardTabs.studyDisclosure.contains("own")
          && WildcardTabs.studyDisclosure.contains("Study only"), "Study says its material is written in the cloud, with the person's key")
    check(WildcardTabs.meetNote.contains("asks for nothing"), "Meet asks for nothing in setup")

    // Live changes post a notification the strip listens for.
    var posted = 0
    let observer = NotificationCenter.default.addObserver(forName: .harborWildcardTabsChanged, object: nil, queue: nil) { _ in posted += 1 }
    WildcardTabs.set([.meet], defaults: defaults)
    WildcardTabs.set([.meet, .study], defaults: defaults)
    NotificationCenter.default.removeObserver(observer)
    check(posted == 2, "changing the tabs tells the strip")
}
