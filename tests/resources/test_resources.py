import json
import tempfile
import unittest
from pathlib import Path

from lib.resources import (
    Resource,
    ResourceCatalog,
    ResourceValidationError,
    build_context,
    import_public_apis,
    load_catalog,
    verify_resource,
)


ROOT = Path(__file__).resolve().parents[2]


PUBLIC_APIS_SAMPLE = """# Public APIs

This deliberately long introduction is not candidate metadata. """ + ("noise " * 500) + """

### Animals

| API | Description | Auth | HTTPS | CORS |
| --- | --- | --- | --- | --- |
| [Cat Facts](https://catfact.ninja) | Daily cat facts | No | Yes | Yes |
| [Pet Store](http://pets.invalid/docs) | Example pet service | `apiKey` | No | Unknown |

### Development

| API | Description | Auth | HTTPS | CORS |
| --- | --- | --- | --- | --- |
| [GitHub](https://docs.github.com/rest) | Repository automation | OAuth | Yes | Yes |
"""


class ResourceSchemaTests(unittest.TestCase):
    def test_metadata_is_compact_and_strictly_validated(self):
        resource = Resource.from_mapping({
            "category": "Development",
            "name": "GitHub REST",
            "description": "Repository automation API.",
            "auth_type": "oauth2",
            "https": True,
            "source": "galaxy-builtin",
            "source_commit": "builtin-v1",
            "verification_status": "verified",
            "official_docs_location": "https://docs.github.com/rest",
        })
        self.assertEqual(resource.name, "GitHub REST")
        self.assertEqual(resource.to_mapping()["https"], True)
        with self.assertRaises(ResourceValidationError):
            Resource.from_mapping({**resource.to_mapping(), "official_docs_location": "http://docs.invalid"})
        with self.assertRaises(ResourceValidationError):
            Resource.from_mapping({**resource.to_mapping(), "verification_status": "trusted"})

    def test_builtin_registry_is_small_deterministic_and_verified(self):
        first = load_catalog(ROOT / "registry/resources.json")
        second = ResourceCatalog(reversed(first.resources))
        self.assertGreaterEqual(len(first.resources), 3)
        self.assertLessEqual(len(first.resources), 8)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertTrue(all(item.verification_status == "verified" for item in first.resources))

    def test_catalog_rejects_non_resource_entries_with_domain_error(self):
        with self.assertRaises(ResourceValidationError):
            ResourceCatalog([{"name": "not-validated"}])


class PublicApisImporterTests(unittest.TestCase):
    def test_local_markdown_import_is_compact_and_always_unverified(self):
        resources = import_public_apis(PUBLIC_APIS_SAMPLE, source_commit="abc123")
        self.assertEqual([item.name for item in resources], ["Cat Facts", "Pet Store", "GitHub"])
        self.assertTrue(all(item.verification_status == "unverified" for item in resources))
        self.assertEqual(resources[0].category, "Animals")
        self.assertEqual(resources[1].auth_type, "api_key")
        self.assertFalse(resources[1].https)
        serialized = json.dumps([item.to_mapping() for item in resources])
        self.assertNotIn("deliberately long introduction", serialized)
        self.assertLess(len(serialized), 1400)

    def test_importer_reads_local_file_but_rejects_network_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "README.md"
            path.write_text(PUBLIC_APIS_SAMPLE, encoding="utf-8")
            from_file = import_public_apis(path, source_commit="def456")
        self.assertEqual(len(from_file), 3)
        with self.assertRaises(ResourceValidationError):
            import_public_apis("https://raw.githubusercontent.com/public-apis/public-apis/master/README.md", source_commit="x")

    def test_verification_requires_explicit_https_official_documentation(self):
        candidate = import_public_apis(PUBLIC_APIS_SAMPLE, source_commit="abc123")[0]
        verified = verify_resource(candidate, "https://catfact.ninja/docs")
        self.assertEqual(candidate.verification_status, "unverified")
        self.assertEqual(verified.verification_status, "verified")
        self.assertEqual(verified.official_docs_location, "https://catfact.ninja/docs")
        with self.assertRaises(ResourceValidationError):
            verify_resource(candidate, "http://catfact.ninja/docs")


class ResourceSearchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ResourceCatalog([
            Resource("Weather", "Open-Meteo", "Weather forecasts without a key.", "none", True,
                     "galaxy-builtin", "builtin-v1", "verified", "https://open-meteo.com/en/docs"),
            Resource("Development", "GitHub REST", "Repository and pull request automation.", "oauth2", True,
                     "galaxy-builtin", "builtin-v1", "verified", "https://docs.github.com/rest"),
            Resource("Development", "GitLab", "Repository automation candidate.", "oauth2", True,
                     "public-apis/public-apis", "abc", "unverified", "https://docs.gitlab.com/ee/api/rest/"),
        ])

    def test_search_ranking_is_deterministic_relevant_and_limited(self):
        forward = self.catalog.search("github repository", limit=2)
        reverse = ResourceCatalog(reversed(self.catalog.resources)).search("github repository", limit=2)
        self.assertEqual([item.name for item in forward], ["GitHub REST", "GitLab"])
        self.assertEqual(forward, reverse)
        self.assertEqual(self.catalog.search("weather", category="Weather")[0].name, "Open-Meteo")
        self.assertEqual(self.catalog.search("unrelated capability"), ())

    def test_context_has_hard_item_and_character_budget(self):
        ranked = (self.catalog.resources[2], self.catalog.resources[0])
        context = build_context(ranked, max_items=2, max_chars=500)
        self.assertLess(context.index("Open-Meteo"), context.index("GitHub REST"))
        self.assertLessEqual(len(context), 500)
        self.assertIn("verification=", context)
        self.assertLessEqual(context.count("\n- "), 2)
        with self.assertRaises(ValueError):
            build_context(self.catalog.resources, max_items=0, max_chars=280)


if __name__ == "__main__":
    unittest.main()
