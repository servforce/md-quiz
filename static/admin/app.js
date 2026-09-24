import { createAdminApiModule } from "./modules/api.js";
import { createAdminRouterModule } from "./modules/router.js";
import { createAdminShellModule } from "./modules/shell.js";
import { createAdminState } from "./modules/state.js";
import { createAdminNavigationModule } from "./modules/navigation.js";
import { createAdminDashboardModule } from "./modules/pages/dashboard.js";
import { createAdminAssignmentsModule } from "./modules/pages/assignments.js";
import { createAdminCandidatesModule } from "./modules/pages/candidates.js";
import { createAdminJobDescriptionsModule } from "./modules/pages/job-descriptions.js";
import { createAdminLogsModule } from "./modules/pages/logs.js";
import { createAdminQuizAnalyticsModule } from "./modules/pages/quiz-analytics.js";
import { createAdminQuizzesModule } from "./modules/pages/quizzes.js";
import { createAdminStatusModule } from "./modules/pages/status.js";

const register = () => {
  if (!window.Alpine) return;
  window.Alpine.data("adminApp", () => ({
    ...createAdminState(),
    ...createAdminNavigationModule(),
    ...createAdminDashboardModule(),
    ...createAdminApiModule(),
    ...createAdminShellModule(),
    ...createAdminQuizzesModule(),
    ...createAdminQuizAnalyticsModule(),
    ...createAdminCandidatesModule(),
    ...createAdminJobDescriptionsModule(),
    ...createAdminAssignmentsModule(),
    ...createAdminLogsModule(),
    ...createAdminStatusModule(),
    ...createAdminRouterModule(),
  }));
};

if (window.Alpine) {
  register();
} else {
  document.addEventListener("alpine:init", register, { once: true });
}
