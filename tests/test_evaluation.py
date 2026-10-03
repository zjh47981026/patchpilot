import unittest
from unittest.mock import patch
from patchpilot import evaluation


class EvaluationTests(unittest.TestCase):
    def test_dataset_contains_positive_clean_and_robustness_controls(self):
        dataset = evaluation.cases()
        self.assertGreaterEqual(len(dataset), 12)
        self.assertEqual(len({c['id'] for c in dataset}), len(dataset))
        tags = {tag for case in dataset for tag in case.get('tags', [])}
        self.assertTrue({'clean-counterexample', 'deleted-line-control',
                         'prompt-injection-control', 'unsupported-partial-context'} <= tags)
        dataset[0]['expected'].clear()
        self.assertTrue(evaluation.cases()[0]['expected'])

    def test_exact_identity_scoring_counts_wrong_line_as_fp_and_fn(self):
        dataset = [dict(id='bug', name='bug', diff='', files={},
                        expected=[dict(path='x.py', line=2, rule='dynamic-execution')]),
                   dict(id='clean', name='clean', diff='', files={}, expected=[])]
        responses = [dict(findings=[dict(path='x.py', line=3, rule='dynamic-execution')], mode='static'),
                     dict(findings=[], mode='static')]
        with patch.object(evaluation, 'cases', return_value=dataset), \
             patch.object(evaluation, 'review', side_effect=responses):
            result = evaluation.evaluate()
        self.assertEqual((result['true_positives'], result['false_positives'], result['false_negatives']), (0, 1, 1))
        self.assertEqual(result['clean_pass_rate'], 1)
        self.assertEqual(result['f1'], 0)
        self.assertFalse(result['cases'][0]['passed'])

    def test_ai_request_is_visible_even_when_provider_falls_back(self):
        dataset = [dict(id='clean', name='clean', diff='', files={}, expected=[])]
        with patch.object(evaluation, 'cases', return_value=dataset), \
             patch.object(evaluation, 'review', return_value=dict(findings=[], mode='static', warnings=['AI unavailable'])) as reviewer:
            result = evaluation.evaluate(use_ai=True)
        reviewer.assert_called_once_with('', {}, True)
        self.assertEqual(result['mode'], 'static+ai')
        self.assertEqual(result['actual_modes'], ['static'])
        self.assertIn('AI unavailable', result['warnings'])

    def test_category_totals_and_clean_controls_are_consistent(self):
        result = evaluation.evaluate()
        for metric in ('true_positives', 'false_positives', 'false_negatives'):
            self.assertEqual(result[metric], sum(row[metric] for row in result['per_category']))
        self.assertEqual({row['rule'] for row in result['per_category']},
                         {'mutable-default', 'dynamic-execution', 'shell-true', 'bare-except', 'unsafe-yaml-load'})
        controls = result['clean_controls']
        self.assertEqual(controls['total'], controls['passed'] + controls['failed'])
        self.assertEqual(controls['excluded'], 1)

    def test_ai_findings_are_reported_without_distorting_static_metrics(self):
        label = dict(path='x.py', line=2, rule='dynamic-execution')
        dataset = [dict(id='bug', name='example', diff='', files={}, expected=[label])]
        result_data = dict(findings=[dict(label, source='static'),
                                    dict(path='x.py', line=3, rule='ai-review', source='ai')], mode='ai')
        with patch.object(evaluation, 'cases', return_value=dataset), \
             patch.object(evaluation, 'review', return_value=result_data):
            result = evaluation.evaluate(use_ai=True)
        self.assertEqual(result['precision'], 1)
        self.assertEqual(result['false_positives'], 0)
        self.assertEqual(result['ai_findings_unscored'], 1)
        self.assertEqual(result['cases'][0]['ai_findings_unscored'], 1)
        self.assertIn('static', result['metrics_scope'])

    def test_demo_exercises_three_distinct_rules(self):
        demo = evaluation.demo()
        result = evaluation.review(demo['diff'], demo['files'], False)
        self.assertTrue({'mutable-default', 'dynamic-execution', 'shell-true'} <=
                        {finding['rule'] for finding in result['findings']})


if __name__ == '__main__':
    unittest.main()
