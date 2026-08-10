from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from data_processor.models import StandardTimeMaster, ChangeoverRule

class StandardTimeAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Create a test user for authentication if needed
        User = get_user_model()
        self.user = User.objects.create_user(email='test@example.com', password='password')
        self.client.force_authenticate(user=self.user)
        
        # Setup base records (is_custom=False)
        self.base_time = StandardTimeMaster.objects.create(
            changeover_key='Fabric to Fabric', 
            standard_time=10.0, 
            is_custom=False
        )
        
        # Setup custom records (is_custom=True)
        self.custom_time = StandardTimeMaster.objects.create(
            changeover_key='COMPOUND ONLY', 
            standard_time=25.0, 
            is_custom=True
        )

    def test_get_standard_times(self):
        response = self.client.get('/api/standard-time/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_create_custom_standard_time(self):
        payload = [
            {
                "changeover_key": "NEW CUSTOM",
                "standard_time": 35.0
            }
        ]
        response = self.client.post('/api/standard-time/', data=payload, format='json')
        if response.status_code == status.HTTP_400_BAD_REQUEST:
             # Our updated view handles single dict or list of dicts. If list fails, try single.
             response = self.client.post('/api/standard-time/', data=payload[0], format='json')
             
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        new_master = StandardTimeMaster.objects.get(changeover_key="NEW CUSTOM")
        self.assertTrue(new_master.is_custom)
        self.assertEqual(new_master.standard_time, 35.0)

    def test_update_existing_standard_time(self):
        payload = [
            {
                "changeover_key": "Fabric to Fabric",
                "standard_time": 15.0
            }
        ]
        response = self.client.post('/api/standard-time/', data=payload, format='json')
        if response.status_code == status.HTTP_400_BAD_REQUEST:
             response = self.client.post('/api/standard-time/', data=payload[0], format='json')
             
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.base_time.refresh_from_db()
        self.assertEqual(self.base_time.standard_time, 15.0)

    def test_delete_base_standard_time_fails(self):
        response = self.client.delete(f'/api/standard-time/{self.base_time.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(StandardTimeMaster.objects.filter(id=self.base_time.id).exists())

    def test_delete_custom_standard_time_succeeds(self):
        # Create a rule attached to custom time to verify cascading delete
        ChangeoverRule.objects.create(
            standard_time_master=self.custom_time,
            from_recipe="A",
            to_recipe="B"
        )
        
        response = self.client.delete(f'/api/standard-time/{self.custom_time.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(StandardTimeMaster.objects.filter(id=self.custom_time.id).exists())
        self.assertFalse(ChangeoverRule.objects.filter(from_recipe="A").exists())

    def test_add_rule_to_standard_time(self):
        payload = {
            "from_recipe": "R1",
            "to_recipe": "R2"
        }
        response = self.client.post(f'/api/standard-time/{self.custom_time.id}/rules/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(ChangeoverRule.objects.filter(standard_time_master=self.custom_time, from_recipe="R1").exists())

    def test_add_duplicate_rule_fails(self):
        ChangeoverRule.objects.create(
            standard_time_master=self.custom_time,
            from_recipe="R1",
            to_recipe="R2"
        )
        payload = {
            "from_recipe": "R1",
            "to_recipe": "R2"
        }
        response = self.client.post(f'/api/standard-time/{self.custom_time.id}/rules/', data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_rule(self):
        rule = ChangeoverRule.objects.create(
            standard_time_master=self.custom_time,
            from_recipe="R1",
            to_recipe="R2"
        )
        response = self.client.delete(f'/api/standard-time/{self.custom_time.id}/rules/{rule.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChangeoverRule.objects.filter(id=rule.id).exists())
