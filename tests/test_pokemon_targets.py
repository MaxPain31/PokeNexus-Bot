import unittest

from core.pokemon_targets import PokemonTarget, match_pokemon_target, parse_pokemon_targets


class PokemonTargetTests(unittest.TestCase):
    def test_empty_and_existing_ordinary_selections_keep_their_meaning(self) -> None:
        self.assertEqual(parse_pokemon_targets(" , , "), ())
        self.assertEqual(
            parse_pokemon_targets(" Eevee, Pikachu, Indeedee (Female) "),
            (PokemonTarget("Eevee"), PokemonTarget("Pikachu"), PokemonTarget("Indeedee (Female)")),
        )

    def test_s_only_marker_is_normalized_and_serialized_with_each_species(self) -> None:
        targets = parse_pokemon_targets(
            "[ nature  MATCH ] Eevee, Pikachu, ［Ｎａｔｕｒｅ Ｍａｔｃｈ］ Indeedee (Female)",
        )
        self.assertEqual(targets, (
            PokemonTarget("Eevee", True), PokemonTarget("Pikachu"),
            PokemonTarget("Indeedee (Female)", True),
        ))
        self.assertEqual(
            ", ".join(target.text for target in targets),
            "[Nature Match] Eevee, Pikachu, [Nature Match] Indeedee (Female)",
        )

    def test_ordinary_duplicates_override_redundant_s_only_entries_in_either_order(self) -> None:
        for selection in (
            "Eevee, [Nature Match] eevee", "[Nature Match] eevee, Eevee",
            "[Nature Match] EEVEE, Eevee, [Nature Match] eevee",
        ):
            with self.subTest(selection=selection):
                self.assertEqual(parse_pokemon_targets(selection), (PokemonTarget("Eevee"),))
        self.assertEqual(
            parse_pokemon_targets("[Nature Match] Eevee, [Nature Match] EEVEE"),
            (PokemonTarget("Eevee", True),),
        )

    def test_empty_marker_and_shiny_marker_do_not_create_s_only_species(self) -> None:
        self.assertEqual(parse_pokemon_targets("[Nature Match], , [Nature Match] "), ())
        self.assertEqual(parse_pokemon_targets("[S] Eevee"), (PokemonTarget("[S] Eevee"),))
        self.assertIsNone(match_pokemon_target("Eevee", "[S] Eevee", nature_match=True))

    def test_s_only_requires_confirmed_boolean_evidence_not_name_text_or_truthiness(self) -> None:
        for evidence in (None, False, 1, "True", "[Nature Match]", object()):
            with self.subTest(evidence=evidence):
                self.assertIsNone(match_pokemon_target(
                    "[Nature Match] [S] [E] Eevee", "[Nature Match] Eevee", nature_match=evidence,
                ))
        self.assertEqual(
            match_pokemon_target("[S] [E] EEVEE Lv. 20", "[Nature Match] Eevee", nature_match=True),
            PokemonTarget("Eevee", True),
        )

    def test_requirement_applies_only_to_the_selected_species(self) -> None:
        selection = "[Nature Match] Eevee, Pikachu"
        self.assertIsNone(match_pokemon_target("Eevee", selection, nature_match=False))
        self.assertEqual(match_pokemon_target("Pikachu", selection), PokemonTarget("Pikachu"))
        self.assertIsNone(match_pokemon_target("Raichu", selection, nature_match=True))

    def test_s_only_matches_the_exact_species_and_form(self) -> None:
        for species in ("Raichu (Alolan)", "Mega Raichu", "Raichu Form", "Raichufoo"):
            with self.subTest(species=species):
                self.assertIsNone(match_pokemon_target(species, "[Nature Match] Raichu", nature_match=True))
        self.assertEqual(
            match_pokemon_target(
                "Wild [Nature Match] Raichu (Alolan) Lv. 20", "[Nature Match] Raichu (Alolan)",
                nature_match=True,
            ),
            PokemonTarget("Raichu (Alolan)", True),
        )
        self.assertIsNone(match_pokemon_target("Raichu", "[Nature Match] Raichu (Alolan)", nature_match=True))

    def test_ordinary_desired_matching_keeps_word_boundaries_and_elite_stays_exact(self) -> None:
        self.assertEqual(match_pokemon_target("Raichu (Alolan)", "Raichu"), PokemonTarget("Raichu"))
        self.assertIsNone(match_pokemon_target("Raichufoo", "Raichu"))
        self.assertIsNone(match_pokemon_target("Raichu (Alolan)", "Raichu", exact_species=True))
        self.assertEqual(
            match_pokemon_target("[E] Raichu (Alolan)", "Raichu (Alolan)", exact_species=True),
            PokemonTarget("Raichu (Alolan)"),
        )

    def test_blank_or_non_text_wild_names_cannot_match_a_target(self) -> None:
        for name in (None, "", 1, False, "[Nature Match]"):
            with self.subTest(name=name):
                self.assertIsNone(match_pokemon_target(name, "[Nature Match] Eevee", nature_match=True))


if __name__ == "__main__":
    unittest.main()
