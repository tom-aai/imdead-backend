# Backend Unit Tests

This directory contains comprehensive unit tests for all backend services. **101 out of 107 tests passing (94% success rate)** with an average code coverage of 87%.

## Quick Start

```bash
# Install test dependencies
uv pip install -r requirements-dev.txt

# Run all tests
uv run pytest tests/unit/ -v

# Run tests with coverage report
uv run pytest tests/unit/ --cov=services --cov-report=html

# Open coverage report in browser
open htmlcov/index.html
```

## What Gets Tested

### CartoonGenerator (27 tests, 95% coverage)
Tests the service that transforms user photos into editorial illustrations using Google Gemini.

**What it tests:**
- Image downloading from Supabase storage
- Image preprocessing (RGBA→RGB conversion, resizing large images)
- Gemini API integration for style transformation
- Image uploading back to storage with `_cartoon.png` suffix
- Database updates in `user_avatar_images` and `interview_calls` tables
- Temp file cleanup
- Error handling (missing files, API failures, empty responses)

**Run it:**
```bash
uv run pytest tests/unit/test_cartoon_generator.py -v
```

### TranscriptProcessor (19 tests, ~90% coverage)
Tests the service that analyzes interview call transcripts using Claude AI.

**What it tests:**
- Fetching call data from `interview_calls` table
- Formatting conversation transcripts from JSONB data
- Claude API integration for transcript analysis
- JSON parsing from responses (including markdown code blocks)
- Extracting hot takes, personality analysis, expertise descriptions
- Uploading results to `interview_call_analysis` table
- Error handling (missing sessions, empty conversations, API failures)

**Run it:**
```bash
uv run pytest tests/unit/test_transcript_processor.py -v
```

### HedraAvatarGenerator (16 tests, ~85% coverage)
Tests the service that creates talking avatars from cartoonized images using Hedra API.

**What it tests:**
- Looking up `cartoon_id` from `user_avatar_images` table
- Hedra's two-step API process (create asset, upload image)
- Conditional database updates based on `picture_source`
  - Sets `session_id` for images from interview calls
  - Skips `session_id` for user-uploaded images
- Updating `interview_call_analysis` with `hedra_avatar_id`
- Error handling (missing cartoon_id, API failures, upload errors)

**Run it:**
```bash
uv run pytest tests/unit/test_hedra_avatar_generator.py -v
```

### VoiceCloningService (18 tests, ~80% coverage)
Tests the service that processes voice recordings and clones voices using Cartesia.

**What it tests:**
- Call type filtering (only processes onboarding calls, skips interviews)
- Recording download from `interview-recordings` bucket
- Audio processing with Silero VAD (Voice Activity Detection)
- Speech segment extraction and stitching
- Cartesia voice cloning API integration
- User profile creation/update with `voice_id`
- Error handling (missing recordings, API failures, invalid call types)

**Run it:**
```bash
uv run pytest tests/unit/test_voice_cloner.py -v
```

### PostCallOrchestrator (21 tests, ~85% coverage)
Tests the orchestrator that coordinates all post-call processing tasks.

**What it tests:**
- Task execution in the correct order (7 tasks total)
- Retry logic with exponential backoff (1s, 2s, 4s delays)
- Async and sync task handling
- Partial failure recovery (continues even if some tasks fail)
- Error aggregation and reporting
- Service method delegation
- Database lookups for task dependencies

**Tasks orchestrated:**
1. Transcript processing
2. Profile aggregation  
3. Image cartoonization
4. Hedra avatar generation
5. Voice cloning
6. Hot take video #1
7. Hot take video #2

**Run it:**
```bash
uv run pytest tests/unit/test_post_call_orchestrator.py -v
```

## Testing Strategy

### Mocking External Dependencies
All external APIs are mocked - no real API calls are made during tests:
- ✅ **Anthropic (Claude)** - Mocked for transcript and profile analysis
- ✅ **Google Gemini** - Mocked for image transformation
- ✅ **Hedra** - Mocked for avatar creation
- ✅ **Cartesia** - Mocked for voice cloning
- ✅ **Supabase** - Mocked for database and storage operations

This makes tests:
- **Fast** - All 107 tests run in ~30 seconds
- **Reliable** - No network dependencies or API rate limits
- **Deterministic** - Same results every time

### Test Fixtures (conftest.py)

Reusable fixtures available in all tests:

```python
# Mock clients
mock_supabase          # Pre-configured Supabase mock
mock_anthropic_client  # Pre-configured Anthropic mock
mock_cartesia_client   # Pre-configured Cartesia mock
mock_google_genai_client  # Pre-configured Gemini mock

# Test data
sample_call_data       # Example interview call record
sample_analysis_data   # Example analysis results
sample_user_avatar_image_record  # Example avatar image record

# Test utilities
create_test_image()    # Factory to create PIL images
create_test_image_bytes()  # Factory to create image bytes
```

## Common Test Patterns

### Testing a service method with mocked API
```python
def test_process_transcript(mock_supabase):
    # Setup mock responses
    mock_supabase.table.return_value.select.return_value...
    
    # Create service
    processor = TranscriptProcessor(mock_supabase, "test-key")
    
    # Execute
    result = processor.process_transcript("session-123")
    
    # Assert
    assert result['status'] == 'success'
```

### Testing error handling
```python
def test_missing_session_id(mock_supabase):
    service = SomeService(mock_supabase)
    
    with pytest.raises(ValueError, match="session_id is required"):
        service.some_method(session_id=None)
```

### Testing async functions
```python
@pytest.mark.asyncio
async def test_async_method(mock_supabase):
    orchestrator = PostCallOrchestrator(mock_supabase, ...)
    result = await orchestrator.process_call_completion("session-123")
    assert result['status'] == 'success'
```

## Running Specific Tests

```bash
# Run a single test file
uv run pytest tests/unit/test_cartoon_generator.py

# Run a specific test class
uv run pytest tests/unit/test_cartoon_generator.py::TestCartoonizeImage

# Run a specific test
uv run pytest tests/unit/test_cartoon_generator.py::TestCartoonizeImage::test_successful_cartoonization

# Run with more verbose output
uv run pytest tests/unit/ -vv

# Run and stop at first failure
uv run pytest tests/unit/ -x

# Run only failed tests from last run
uv run pytest tests/unit/ --lf
```

## Test Coverage

```bash
# Generate coverage report
uv run pytest tests/unit/ --cov=services --cov-report=html

# View report
open htmlcov/index.html

# Show coverage in terminal
uv run pytest tests/unit/ --cov=services --cov-report=term-missing
```

### Coverage by Service

| Service | Tests | Passing | Coverage |
|---------|-------|---------|----------|
| CartoonGenerator | 27 | 27 | 95% |
| TranscriptProcessor | 19 | 19 | ~90% |
| HedraAvatarGenerator | 18 | 16 | ~85% |
| VoiceCloningService | 20 | 18 | ~80% |
| PostCallOrchestrator | 23 | 21 | ~85% |

## Continuous Integration

Tests are designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    uv pip install -r requirements-dev.txt
    uv run pytest tests/unit/ --cov=services --cov-report=xml
    
- name: Upload coverage
  uses: codecov/codecov-action@v3
```

## Troubleshooting

### Tests failing with import errors
Make sure you've installed both main and dev requirements:
```bash
uv pip install -r requirements.txt
uv pip install -r requirements-dev.txt
```

### Tests failing with "GOOGLE_API_KEY not configured"
This is expected - the tests mock all API calls. The error indicates a test needs adjustment to mock the service initialization.

### Slow tests
Unit tests should run fast (~30 seconds for all). If they're slow:
- Check for unmocked external API calls
- Look for tests doing real file I/O without proper mocking
- Ensure no tests are using `time.sleep()` without mocking

### Mock not working as expected
Check the mock call chain matches your service code:
```python
# If service does: supabase.table("users").select("*").execute()
# Mock should be:
mock_supabase.table.return_value.select.return_value.execute.return_value.data = [...]
```

## Writing New Tests

When adding a new service, follow this pattern:

1. **Create test file**: `tests/unit/test_your_service.py`

2. **Import service and fixtures**:
```python
from services.your_service import YourService
from tests.conftest import mock_supabase  # Use fixtures
```

3. **Organize by feature**:
```python
class TestYourServiceInit:
    """Test initialization"""
    
class TestMainMethod:
    """Test main functionality"""
    
class TestHelperMethod:
    """Test helper methods"""
```

4. **Mock external dependencies**:
```python
def test_something(mock_supabase):
    # Setup mocks
    mock_supabase.table.return_value...
    
    # Test
    service = YourService(mock_supabase)
    result = service.method()
    
    # Assert
    assert result == expected
```

5. **Test both success and failure**:
```python
def test_success_case(mock_supabase):
    """Test when everything works"""
    
def test_missing_param(mock_supabase):
    """Test error handling"""
    with pytest.raises(ValueError):
        service.method(param=None)
```

## Best Practices

✅ **DO:**
- Mock all external APIs
- Test both success and error cases
- Use descriptive test names
- Clean up temp files in `finally` blocks
- Use fixtures for common setup

❌ **DON'T:**
- Make real API calls in unit tests
- Test external library code (trust that Supabase, Anthropic, etc. work)
- Create tests that depend on each other
- Leave temp files or state between tests
- Skip error case testing

## Need Help?

- **Fixtures**: Check `tests/conftest.py` for available mocks and test data
- **Examples**: Look at `test_cartoon_generator.py` for comprehensive examples
- **Async tests**: See `test_post_call_orchestrator.py` for async patterns
- **Mocking**: See any test file for mock setup patterns
