# Generated by Django 5.0.1 on 2025-01-05 02:39

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='activity_level',
            field=models.CharField(choices=[('low', 'Low'), ('moderate', 'Moderate'), ('high', 'High')], default='moderate', max_length=50),
        ),
    ]
