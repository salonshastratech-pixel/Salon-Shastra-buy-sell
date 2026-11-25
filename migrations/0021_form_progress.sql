-- Forms are saved as people fill them: how many questions are answered, and the answers given so far (until the
-- form is finished, when they become a normal response). Shown as "Partly filled (12 of 45)" for the team.
ALTER TABLE pilot_profiles ADD COLUMN form_answered INTEGER;
ALTER TABLE pilot_profiles ADD COLUMN form_questions INTEGER;
ALTER TABLE pilot_profiles ADD COLUMN partial_answers TEXT;
ALTER TABLE investor_profiles ADD COLUMN form_answered INTEGER;
ALTER TABLE investor_profiles ADD COLUMN form_questions INTEGER;
ALTER TABLE investor_profiles ADD COLUMN partial_answers TEXT;
