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

    // Persona to tabs: at most two, only Meet, Study or Ideate, no repeats.
    let expected: [(QuaysidePersona, [NotchTab], IdeateKind?)] = [
        (.studentHighSchool, [.study], nil),
        (.studentCollege, [.study], nil),
        (.studentGrad, [.study, .meet], nil),
        (.builderUI, [.ideate], .ui),
        (.builderDebug, [.ideate], .debug),
        (.builderLearn, [.ideate, .study], .learn),
        (.builderGeneral, [.ideate], .general),
        (.builderElse, [.ideate], .script),
        (.meetings, [.meet], nil),
        (.manager, [.meet], nil),
        (.creator, [.ideate], .general),
        (.home, [], nil),
        (.basics, [], nil),
    ]
    check(expected.count == QuaysidePersona.allCases.count, "the mapping covers every persona")
    for (persona, tabs, kind) in expected {
        check(persona.suggestedTabs == tabs, "\(persona) suggests \(tabs)")
        check(persona.ideateKind == kind, "\(persona) starts Ideate as \(String(describing: kind))")
        check(persona.suggestedTabs.count <= 2, "\(persona) suggests two tabs at most")
        check(persona.suggestedTabs.allSatisfy(WildcardTabs.candidates.contains), "\(persona) suggests only wildcard tabs")
        check((persona.ideateKind != nil) == persona.suggestedTabs.contains(.ideate), "\(persona): a variant exactly when Ideate is suggested")
    }

    // The validator: wildcard tabs only, once each, two at most, in order.
    check(WildcardTabs.validated([]) == [], "none is allowed")
    check(WildcardTabs.validated([.study, .study, .meet, .ideate]) == [.study, .meet], "dedupe, then two, in order")
    check(WildcardTabs.validated([.ask, .brief, .day, .research, .ideate]) == [.ideate], "Ask, Brief, Day and Research never join")
    check(WildcardTabs.validated([.ideate, .meet, .study]) == [.ideate, .meet], "the first two win")

    // Change: untick to free a slot; a third is refused.
    check(WildcardTabs.toggled(.ideate, in: []) == [.ideate], "a free tab joins")
    check(WildcardTabs.toggled(.study, in: [.ideate]) == [.ideate, .study], "a second joins at the end")
    check(WildcardTabs.toggled(.meet, in: [.ideate, .study]) == [.ideate, .study], "a third is refused")
    check(!WildcardTabs.canAdd(.meet, to: [.ideate, .study]) && WildcardTabs.canAdd(.meet, to: [.ideate]), "canAdd follows the limit")
    check(WildcardTabs.toggled(.ideate, in: [.ideate, .study]) == [.study], "a chosen tab leaves")
    check(WildcardTabs.toggled(.ask, in: [.study]) == [.study], "a fixed tab can't be toggled")

    // The saved choice: unset keeps Meet and Study; an empty list is a real choice.
    check(WildcardTabs.chosen(defaults: defaults) == [.meet, .study] && !WildcardTabs.wasChosen(defaults: defaults),
          "never chosen: Meet and Study, so nothing changes for an existing install")
    check(WildcardTabs.set([.ideate, .study, .meet], defaults: defaults) == [.ideate, .study]
          && WildcardTabs.chosen(defaults: defaults) == [.ideate, .study] && WildcardTabs.wasChosen(defaults: defaults),
          "a choice is validated, saved and read back in order")
    check(defaults.stringArray(forKey: WildcardTabs.key) == ["ideate", "study"], "saved as ids, not numbers")
    WildcardTabs.set([], defaults: defaults)
    check(WildcardTabs.chosen(defaults: defaults) == [] && WildcardTabs.wasChosen(defaults: defaults), "none chosen stays none")
    defaults.set(["study", "nonsense", "study", "ask"], forKey: WildcardTabs.key)
    check(WildcardTabs.chosen(defaults: defaults) == [.study], "junk ids and repeats are dropped")
    defaults.set("study", forKey: WildcardTabs.key)
    check(WildcardTabs.chosen(defaults: defaults) == [.meet, .study], "a value of the wrong type counts as never chosen")
    check(WildcardTabs.ideateIsOn(defaults: defaults) == false, "Ideate is off when not chosen")
    check(WildcardTabs.key == "harbor.wildcardTabs" && QuaysidePersonaStore.ideateKindKey == "harbor.ideate.kind",
          "the saved keys are the ones the Ideate model and the plan name")

    // The persona store applies tabs and the Ideate variant together.
    defaults.removePersistentDomain(forName: suite)
    check(QuaysidePersonaStore.persona(defaults: defaults) == nil && QuaysidePersonaStore.ideateKind(defaults: defaults) == .general,
          "no persona and the general variant until chosen")
    QuaysidePersonaStore.choose(.builderDebug, defaults: defaults)
    check(QuaysidePersonaStore.persona(defaults: defaults) == .builderDebug
          && QuaysidePersonaStore.ideateKind(defaults: defaults) == .debug
          && WildcardTabs.chosen(defaults: defaults) == [.ideate], "a builder gets Ideate and its variant")
    QuaysidePersonaStore.setIdeateKind(.script, defaults: defaults)
    check(QuaysidePersonaStore.ideateKind(defaults: defaults) == .script, "the variant can change later")
    QuaysidePersonaStore.choose(.studentGrad, defaults: defaults)
    check(WildcardTabs.chosen(defaults: defaults) == [.study, .meet]
          && QuaysidePersonaStore.ideateKind(defaults: defaults) == .script,
          "a persona without Ideate leaves the variant as it was")
    QuaysidePersonaStore.choose(.basics, defaults: defaults)
    check(WildcardTabs.chosen(defaults: defaults).isEmpty, "Just the basics: Ask, Brief and Day")
    defaults.set("bogus", forKey: QuaysidePersonaStore.personaKey)
    defaults.set("bogus", forKey: QuaysidePersonaStore.ideateKindKey)
    check(QuaysidePersonaStore.persona(defaults: defaults) == nil && QuaysidePersonaStore.ideateKind(defaults: defaults) == .general,
          "unknown saved ids fall back")

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
    QuaysidePersonaStore.setIdeateKind(.ui, defaults: defaults)
    NotificationCenter.default.removeObserver(observer)
    check(posted == 2, "changing the tabs or the variant tells the strip")
}
