import React from 'react';
import { ProjectsSection } from '@/features/projects/ProjectsSection';
import { ActivitySection } from '@/features/activity/ActivitySection';
import { TrialSection } from '@/features/trial/TrialSection';

export const HomePage: React.FC = () => {
  return (
    <div className="space-y-12 pb-16">
      {/* 1. Projects Section includes Header, Favorites, and All Projects */}
      <ProjectsSection />

      {/* 2. Recent Activity Section */}
      <ActivitySection />

      {/* 3. Trial Section */}
      <TrialSection />
    </div>
  );
};

export default HomePage;
