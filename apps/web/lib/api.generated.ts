// Generated from apps/api/openapi.json. Do not edit. Run pnpm api:generate.
export interface paths {
    "/api/courses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search Courses */
        get: operations["search_courses_api_courses_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/courses/{code}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Course */
        get: operations["get_course_api_courses__code__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/courses/{code}/sections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Course Sections */
        get: operations["get_course_sections_api_courses__code__sections_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/plan/generate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Generate Degree Plan
         * @description Stateless plan generation. Accepts a ParsedDegreeValidated (from /api/plan/parse)
         *     and student preferences, returns a semester-by-semester plan.
         *     Nothing is stored server-side — the client persists the result to localStorage.
         */
        post: operations["generate_degree_plan_api_plan_generate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/plan/ger-courses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Ger Courses */
        get: operations["ger_courses_api_plan_ger_courses_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/plan/parse": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Parse Degree Works
         * @description Stateless PDF parsing endpoint. Receives a base64-encoded DegreeWorks PDF,
         *     extracts structured degree data, and returns it as JSON. Nothing is stored
         *     server-side — the client persists the result to localStorage.
         */
        post: operations["parse_degree_works_api_plan_parse_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/professors/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Professor */
        get: operations["get_professor_api_professors__name__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/schedule/solve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Solve Schedule */
        post: operations["solve_schedule_api_schedule_solve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/scraper/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Scraper Status
         * @description Returns the last Banner scrape timestamp and status.
         *     Used by the frontend to show a staleness warning when seat data is old.
         */
        get: operations["scraper_status_api_scraper_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/version": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Version */
        get: operations["version_api_version_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** CommuterOptions */
        CommuterOptions: {
            /** Blocked Days */
            blocked_days?: string[];
            /** Earliest Start */
            earliest_start?: string | null;
            /**
             * Hide Full Sections
             * @default false
             */
            hide_full_sections?: boolean;
            /** Latest End */
            latest_end?: string | null;
            /**
             * Minimize Gaps
             * @default false
             */
            minimize_gaps?: boolean;
        };
        /** CourseDetailResponse */
        CourseDetailResponse: {
            /** Course Code */
            course_code: string;
            /** Credits */
            credits: number;
            /** Prerequisites */
            prerequisites: string[];
            /** Sections */
            sections: components["schemas"]["SectionResponse"][];
            /** Title */
            title: string | null;
        };
        /** CourseResponse */
        CourseResponse: {
            /** Course Code */
            course_code: string;
            /** Credits */
            credits: number;
            /** Title */
            title: string | null;
        };
        /** DegradedHealthResponse */
        DegradedHealthResponse: {
            /**
             * Error
             * @constant
             */
            error: "db_unreachable";
            /**
             * Status
             * @constant
             */
            status: "degraded";
        };
        /** GeneratedPlan */
        GeneratedPlan: {
            /** Projected Graduation */
            projected_graduation: string;
            /** Semesters */
            semesters: components["schemas"]["SemesterCard"][];
            /** Warnings */
            warnings: string[];
        };
        /** GenerateRequest */
        GenerateRequest: {
            /** Parsed Degree */
            parsed_degree: {
                [key: string]: unknown;
            };
            /** Preferences */
            preferences: {
                [key: string]: unknown;
            };
        };
        /** GerCourse */
        GerCourse: {
            /** Code */
            code: string;
            /** Title */
            title: string | null;
        };
        /** GerCoursesResponse */
        GerCoursesResponse: {
            /** Groups */
            groups: components["schemas"]["GerGroup"][];
        };
        /** GerGroup */
        GerGroup: {
            /** Courses */
            courses: components["schemas"]["GerCourse"][];
            /** Prefix */
            prefix: string;
        };
        /** HealthResponse */
        HealthResponse: {
            /**
             * Db
             * @constant
             */
            db: "connected";
            /** Env */
            env: string;
            /** Sections */
            sections: number;
            /**
             * Status
             * @constant
             */
            status: "ok";
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** MeetingResponse */
        MeetingResponse: {
            /** Days */
            days: string | null;
            /** End Time */
            end_time: string | null;
            /** Location */
            location: string | null;
            /** Start Time */
            start_time: string | null;
        };
        /**
         * ParsedDegreeValidated
         * @description Produced only by validate_parsed_degree(). Never instantiate directly.
         *     Nothing downstream should accept a raw ParsedDegree.
         */
        ParsedDegreeValidated: {
            /** Catalog Year */
            catalog_year: number | null;
            /** Completed Courses */
            completed_courses: string[];
            /** Credits Completed */
            credits_completed: number | null;
            /** Credits Remaining */
            credits_remaining: number | null;
            /** Credits Required */
            credits_required: number | null;
            /** In Progress Courses */
            in_progress_courses: string[];
            /** Majors */
            majors: string[];
            /** Minors */
            minors: string[];
            /** Still Needed */
            still_needed: components["schemas"]["StillNeededItem"][];
            /** Student Name */
            student_name: string | null;
        };
        /** ParseRequest */
        ParseRequest: {
            /** Client Pdf Hash */
            client_pdf_hash: string;
            /** Pdf Base64 */
            pdf_base64: string;
        };
        /** ParseResponse */
        ParseResponse: {
            parsed: components["schemas"]["ParsedDegreeValidated"];
            /** Server Hash */
            server_hash: string;
            /** Warnings */
            warnings: string[];
        };
        /** PlannedCourse */
        PlannedCourse: {
            /**
             * Badge
             * @enum {string}
             */
            badge: "Required" | "Elective" | "TBD";
            /** Course Code */
            course_code: string;
            /** Credits */
            credits: number;
            /** Reason */
            reason: string;
            /** Title */
            title: string | null;
        };
        /** ProfessorResponse */
        ProfessorResponse: {
            /** Department */
            department: string | null;
            /** Rmp Difficulty */
            rmp_difficulty: number | null;
            /** Rmp Num Ratings */
            rmp_num_ratings: number | null;
            /** Rmp Score */
            rmp_score: number | null;
            /** Rmp Tags */
            rmp_tags: string[];
            /** Rmp Would Take Again */
            rmp_would_take_again: number | null;
        };
        /** ScheduleResult */
        ScheduleResult: {
            /** Campus Days */
            campus_days: number;
            /** Has Async Sections */
            has_async_sections: boolean;
            /** Sections */
            sections: components["schemas"]["SolveSectionResponse"][];
        };
        /** ScraperStatusResponse */
        ScraperStatusResponse: {
            /** Error Message */
            error_message: string | null;
            /** Last Scrape */
            last_scrape: string | null;
            /** Sections Upserted */
            sections_upserted: number | null;
            /**
             * Status
             * @enum {string}
             */
            status: "never_run" | "running" | "completed" | "failed" | "blocked" | "schema_change" | "skipped_overlap";
        };
        /** SectionResponse */
        SectionResponse: {
            /** Course Code */
            course_code: string;
            /** Crn */
            crn: string;
            /** Meetings */
            meetings: components["schemas"]["MeetingResponse"][];
            /** Open Seats */
            open_seats: number;
            /** Professor Name */
            professor_name: string | null;
            /** Scraped At */
            scraped_at: string | null;
            /** Total Seats */
            total_seats: number;
        };
        /** SemesterCard */
        SemesterCard: {
            /** Courses */
            courses: components["schemas"]["PlannedCourse"][];
            /** Term */
            term: string;
            /** Term Label */
            term_label: string;
            /**
             * Total Credits
             * @default 0
             */
            total_credits: number;
        };
        /** SolveRequest */
        SolveRequest: {
            /**
             * Compact Week
             * @default false
             */
            compact_week?: boolean;
            /** Course Codes */
            course_codes: string[];
            options?: components["schemas"]["CommuterOptions"];
            /** Professor Preferences */
            professor_preferences?: {
                [key: string]: string[];
            };
            /** Term */
            term: string;
        };
        /** SolveResponse */
        SolveResponse: {
            /** Results */
            results: components["schemas"]["ScheduleResult"][];
            /** Truncated */
            truncated: boolean;
            /** Warnings */
            warnings: string[];
        };
        /** SolveSectionResponse */
        SolveSectionResponse: {
            /** Course Code */
            course_code: string;
            /** Crn */
            crn: string;
            /** Meetings */
            meetings: components["schemas"]["MeetingResponse"][];
            /** Open Seats */
            open_seats: number;
            /** Professor Name */
            professor_name: string | null;
            /** Scraped At */
            scraped_at: string | null;
            /** Section Number */
            section_number: string | null;
            /** Term */
            term: string;
            /** Total Seats */
            total_seats: number;
        };
        /** StillNeededItem */
        StillNeededItem: {
            /** Options */
            options: string[];
            /** Requirement */
            requirement: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** VersionResponse */
        VersionResponse: {
            /** Env */
            env: string;
            /** Term */
            term: string;
            /** Version */
            version: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    search_courses_api_courses_get: {
        parameters: {
            query?: {
                limit?: number;
                page?: number;
                q?: string | null;
                subject?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseResponse"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_course_api_courses__code__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                code: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_course_sections_api_courses__code__sections_get: {
        parameters: {
            query: {
                term: string;
            };
            header?: never;
            path: {
                code: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SectionResponse"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    generate_degree_plan_api_plan_generate_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GenerateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GeneratedPlan"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ger_courses_api_plan_ger_courses_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GerCoursesResponse"];
                };
            };
        };
    };
    parse_degree_works_api_plan_parse_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ParseRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ParseResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_professor_api_professors__name__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProfessorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    solve_schedule_api_schedule_solve_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SolveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SolveResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    scraper_status_api_scraper_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScraperStatusResponse"];
                };
            };
        };
    };
    version_api_version_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VersionResponse"];
                };
            };
        };
    };
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DegradedHealthResponse"];
                };
            };
        };
    };
}
