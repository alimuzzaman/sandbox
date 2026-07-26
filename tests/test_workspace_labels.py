import unittest
from concurrent.futures import ThreadPoolExecutor

from sandbox.jobs.scheduler import matrix_workspace_label


class WorkspaceLabelTests(unittest.TestCase):
    def test_matrix_labels_are_stable_short_and_project_scoped(self):
        first = matrix_workspace_label(project_identity="one", parent_job_id="a" * 32, cell={"node": 20, "os": "linux"})
        self.assertEqual(first, matrix_workspace_label(project_identity="one", parent_job_id="a" * 32, cell={"os": "linux", "node": 20}))
        self.assertLessEqual(len(first), 21)
        self.assertNotEqual(first, matrix_workspace_label(project_identity="two", parent_job_id="a" * 32, cell={"node": 20, "os": "linux"}))

    def test_parent_attempt_and_canonical_cell_values_keep_labels_concurrent_safe(self):
        base = {"node": 20, "os": "linux"}
        first = matrix_workspace_label(project_identity="project", parent_job_id="a" * 32, cell=base)
        variants = {
            matrix_workspace_label(project_identity="project", parent_job_id="b" * 32, cell=base),
            matrix_workspace_label(project_identity="project", parent_job_id="a" * 32, cell=base, attempt=2),
            matrix_workspace_label(project_identity="project", parent_job_id="a" * 32,
                                   cell={"node": 22, "os": "linux"}),
        }
        self.assertNotIn(first, variants)
        self.assertEqual(len(variants), 3)
        cells = [{"node": node, "os": operating_system}
                 for node in (18, 20, 22) for operating_system in ("linux", "macos", "windows")]
        with ThreadPoolExecutor(max_workers=8) as executor:
            labels = list(executor.map(
                lambda cell: matrix_workspace_label(project_identity="project", parent_job_id="a" * 32, cell=cell),
                cells,
            ))
        self.assertEqual(len(labels), len(set(labels)))
        self.assertTrue(all(len(label) <= 21 for label in labels))
