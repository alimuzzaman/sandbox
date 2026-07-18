import unittest

from sandbox.jobs.scheduler import matrix_workspace_label


class WorkspaceLabelTests(unittest.TestCase):
    def test_matrix_labels_are_stable_short_and_project_scoped(self):
        first = matrix_workspace_label(project_identity="one", parent_job_id="a" * 32, cell={"node": 20, "os": "linux"})
        self.assertEqual(first, matrix_workspace_label(project_identity="one", parent_job_id="a" * 32, cell={"os": "linux", "node": 20}))
        self.assertLessEqual(len(first), 21)
        self.assertNotEqual(first, matrix_workspace_label(project_identity="two", parent_job_id="a" * 32, cell={"node": 20, "os": "linux"}))
